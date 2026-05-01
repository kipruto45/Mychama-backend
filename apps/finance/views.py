import uuid
from decimal import Decimal

from django.core.cache import cache
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.gating import BillingAccessMixin
from apps.chama.models import Chama, MembershipRole
from apps.chama.models import LoanPolicy as ChamaLoanPolicy
from apps.chama.services import get_effective_role, is_member_suspended
from apps.finance.member_contribution_workflow import (
    build_member_contribution_detail,
    build_member_contribution_workspace,
    build_member_penalties,
)
from apps.finance.member_loan_workflow import build_member_loan_workspace
from apps.finance.member_wallet_workflow import (
    build_member_wallet_activity,
    build_member_wallet_transaction_detail,
    build_member_wallet_workspace,
    create_member_wallet_contribution,
    create_member_wallet_deposit,
    create_member_wallet_transfer,
    create_member_wallet_withdrawal,
    get_member_wallet_deposit_detail,
    get_member_wallet_withdrawal_detail,
    refresh_member_wallet_deposit,
    refresh_member_wallet_withdrawal,
)
from apps.finance.models import (
    Contribution,
    ContributionGoal,
    ContributionGoalStatus,
    ContributionType,
    Expense,
    ExpenseCategory,
    FinancialSnapshot,
    InstallmentSchedule,
    JournalEntry,
    LedgerEntry,
    Loan,
    LoanApplication,
    LoanApplicationApproval,
    LoanApplicationGuarantor,
    LoanApprovalLog,
    LoanGuarantor,
    LoanProduct,
    LoanRecoveryAction,
    LoanRestructureRequest,
    LoanTopUpRequest,
    MonthClosure,
    Penalty,
)
from apps.finance.permissions import get_chama_membership
from apps.finance.serializers import (
    AllTransactionsQuerySerializer,
    ChamaLoanPolicySerializer,
    ContributionGoalSerializer,
    ContributionGoalUpsertSerializer,
    ContributionRecordSerializer,
    ContributionSerializer,
    ContributionTypeSerializer,
    CreditScoreQuerySerializer,
    DashboardQuerySerializer,
    ExpenseCategorySerializer,
    ExpenseCreateSerializer,
    ExpenseDecisionSerializer,
    ExpenseMarkPaidSerializer,
    ExpenseSerializer,
    FinancialSnapshotSerializer,
    IdempotencyOnlySerializer,
    InstallmentScheduleSerializer,
    LedgerEntrySerializer,
    LedgerQuerySerializer,
    LedgerReverseSerializer,
    LoanApplicationApprovalSerializer,
    LoanApplicationDecisionSerializer,
    LoanApplicationGuarantorActionSerializer,
    LoanApplicationGuarantorCreateSerializer,
    LoanApplicationGuarantorSerializer,
    LoanApplicationRequestSerializer,
    LoanApplicationSerializer,
    LoanApprovalLogSerializer,
    LoanEligibilitySerializer,
    LoanGuarantorActionSerializer,
    LoanGuarantorCreateSerializer,
    LoanGuarantorSerializer,
    LoanOffsetSerializer,
    LoanPortfolioQuerySerializer,
    LoanProductSerializer,
    LoanRecoveryActionCreateSerializer,
    LoanRecoveryActionSerializer,
    LoanRequestSerializer,
    LoanRestructureRequestCreateSerializer,
    LoanRestructureRequestSerializer,
    LoanRestructureReviewSerializer,
    LoanReviewSerializer,
    LoanSerializer,
    LoanTopUpRequestCreateSerializer,
    LoanTopUpRequestSerializer,
    LoanTopUpReviewSerializer,
    LoanWriteOffSerializer,
    ManualAdjustmentPostSerializer,
    ManualAdjustmentSerializer,
    MemberWalletActivityQuerySerializer,
    MemberWalletDepositCreateSerializer,
    MemberWalletDepositDetailSerializer,
    MemberWalletContributionCreateSerializer,
    MemberWalletTransferCreateSerializer,
    MemberWalletWithdrawalCreateSerializer,
    MemberWalletWithdrawalDetailSerializer,
    MonthCloseSerializer,
    MonthClosureSerializer,
    MonthlyAggregateQuerySerializer,
    PenaltyIssueSerializer,
    PenaltySerializer,
    RepaymentPostSerializer,
    RepaymentSerializer,
    StatementQuerySerializer,
    WalletQuerySerializer,
    WalletSummarySerializer,
)
from apps.finance.transaction_feed import (
    TransactionsFeedFilters,
    get_transaction_detail,
    list_all_transactions,
)
from apps.finance.services import (
    FinanceService,
    FinanceServiceError,
    IdempotencyConflictError,
    MonthClosedError,
)
from apps.payments.unified_models import PaymentIntent
from apps.payments.unified_services import PaymentServiceError, UnifiedPaymentService
from core.api_response import ApiResponse


def _validate_uuid(raw_value, label: str) -> str:
    try:
        return str(uuid.UUID(str(raw_value)))
    except (ValueError, TypeError) as exc:
        raise ValidationError({label: f"Invalid {label}."}) from exc


def _resolve_chama_id(request, explicit_chama_id=None, label: str = "chama_id") -> str:
    """Resolve chama scope from query/body/header/session with conflict checks."""
    raw_sources = [
        explicit_chama_id,
        request.headers.get("X-CHAMA-ID"),
        request.session.get("active_chama_id"),
    ]

    parsed_values: list[str] = []
    for raw in raw_sources:
        if raw in [None, ""]:
            continue
        parsed_values.append(_validate_uuid(raw, label))

    if not parsed_values:
        raise ValidationError(
            {
                label: (
                    "Provide chama_id in query/body, X-CHAMA-ID header, "
                    "or ensure active chama is set in session."
                )
            }
        )

    if len(set(parsed_values)) > 1:
        raise ValidationError(
            {"detail": "Chama scope values do not match across request sources."}
        )

    return parsed_values[0]


def _require_membership(user, chama_id):
    if is_member_suspended(chama_id, user.id):
        raise PermissionDenied("Your membership in this chama is currently suspended.")

    membership = get_chama_membership(user, chama_id)
    if not membership:
        raise PermissionDenied("You are not an approved active member of this chama.")
    return membership


def _require_roles(user, chama_id, allowed_roles: set[str], message: str):
    membership = _require_membership(user, chama_id)
    effective_role = get_effective_role(user, chama_id, membership)
    if effective_role not in allowed_roles:
        raise PermissionDenied(message)
    return membership


class FinanceBaseView(BillingAccessMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    billing_feature_key = "full_finance_management"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)

        # Compliance gate: Tier 0 is view-only; block financial mutations until KYC is approved.
        if request.method in permissions.SAFE_METHODS:
            return

        user = getattr(request, "user", None)
        if user and getattr(user, "account_frozen", False):
            raise PermissionDenied(
                detail={
                    "code": "ACCOUNT_FROZEN",
                    "detail": "Your account has been restricted due to a compliance check.",
                }
            )

        if user and not getattr(user, "financial_access_enabled", False):
            raise PermissionDenied(
                detail={
                    "code": "KYC_REQUIRED",
                    "detail": "Complete KYC verification before using financial features.",
                }
            )

    def _handle_service_error(self, exc: Exception):
        if isinstance(exc, IdempotencyConflictError):
            return Response(
                {
                    "code": "IDEMPOTENCY_CONFLICT",
                    "message": str(exc),
                    "detail": str(exc),
                },
                status=status.HTTP_409_CONFLICT,
            )
        if isinstance(exc, MonthClosedError):
            return Response(
                {
                    "code": "MONTH_CLOSED",
                    "message": str(exc),
                    "detail": str(exc),
                },
                status=status.HTTP_409_CONFLICT,
            )
        if isinstance(exc, FinanceServiceError):
            return Response(
                {
                    "code": "BUSINESS_RULE_VIOLATION",
                    "message": str(exc),
                    "detail": str(exc),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if isinstance(exc, Http404):
            return Response(
                {
                    "code": "NOT_FOUND",
                    "message": "Not found.",
                    "detail": "Not found.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        raise exc


class LoanPolicyListCreateView(FinanceBaseView):
    def get(self, request):
        chama_id = _resolve_chama_id(
            request,
            request.query_params.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)

        queryset = LoanProduct.objects.filter(chama_id=chama_id).order_by("name")
        return Response(LoanProductSerializer(queryset, many=True).data)

    def post(self, request):
        serializer = LoanProductSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        chama_id = _validate_uuid(serializer.validated_data.get("chama_id"), "chama_id")
        _require_roles(
            request.user,
            chama_id,
            {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER},
            "Only treasurer or admin can manage loan policies.",
        )

        if serializer.validated_data.get("is_default", False):
            LoanProduct.objects.filter(chama_id=chama_id, is_default=True).update(
                is_default=False
            )

        loan_policy = LoanProduct.objects.create(
            chama_id=chama_id,
            name=serializer.validated_data["name"],
            is_active=serializer.validated_data.get("is_active", True),
            is_default=serializer.validated_data.get("is_default", False),
            max_loan_amount=serializer.validated_data["max_loan_amount"],
            contribution_multiple=serializer.validated_data.get(
                "contribution_multiple", "0.00"
            ),
            interest_type=serializer.validated_data["interest_type"],
            interest_rate=serializer.validated_data["interest_rate"],
            min_duration_months=serializer.validated_data.get("min_duration_months", 1),
            max_duration_months=serializer.validated_data.get(
                "max_duration_months", 12
            ),
            grace_period_days=serializer.validated_data.get("grace_period_days", 0),
            late_penalty_type=serializer.validated_data["late_penalty_type"],
            late_penalty_value=serializer.validated_data.get(
                "late_penalty_value", "0.00"
            ),
            early_repayment_discount_percent=serializer.validated_data.get(
                "early_repayment_discount_percent",
                "0.00",
            ),
            minimum_membership_months=serializer.validated_data.get(
                "minimum_membership_months", 0
            ),
            minimum_contribution_months=serializer.validated_data.get(
                "minimum_contribution_months", 0
            ),
            block_if_unpaid_penalties=serializer.validated_data.get(
                "block_if_unpaid_penalties", True
            ),
            block_if_overdue_loans=serializer.validated_data.get(
                "block_if_overdue_loans", True
            ),
            require_treasurer_review=serializer.validated_data.get(
                "require_treasurer_review", True
            ),
            require_separate_disburser=serializer.validated_data.get(
                "require_separate_disburser", True
            ),
            created_by=request.user,
            updated_by=request.user,
        )

        return Response(
            LoanProductSerializer(loan_policy).data,
            status=status.HTTP_201_CREATED,
        )


class ChamaLoanPolicyView(FinanceBaseView):
    def get(self, request):
        chama_id = _resolve_chama_id(
            request,
            request.query_params.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        policy, _ = ChamaLoanPolicy.objects.get_or_create(chama_id=chama_id)
        return Response(ChamaLoanPolicySerializer(policy).data)

    def patch(self, request):
        chama_id = _resolve_chama_id(
            request,
            request.data.get("chama_id") or request.query_params.get("chama_id"),
            label="chama_id",
        )
        _require_roles(
            request.user,
            chama_id,
            {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER},
            "Only treasurer or admin can manage loan governance policy.",
        )
        policy, _ = ChamaLoanPolicy.objects.get_or_create(chama_id=chama_id)
        serializer = ChamaLoanPolicySerializer(policy, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(policy, field, value)
        policy.updated_by = request.user
        policy.save()
        return Response(ChamaLoanPolicySerializer(policy).data)


class LoanPolicyDetailView(FinanceBaseView):
    def get_object(self, policy_id):
        return LoanProduct.objects.filter(id=policy_id).first()

    def get(self, request, id):
        loan_policy = self.get_object(id)
        if not loan_policy:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_membership(request.user, str(loan_policy.chama_id))
        return Response(LoanProductSerializer(loan_policy).data)

    def patch(self, request, id):
        loan_policy = self.get_object(id)
        if not loan_policy:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_roles(
            request.user,
            str(loan_policy.chama_id),
            {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER},
            "Only treasurer or admin can manage loan policies.",
        )

        serializer = LoanProductSerializer(
            loan_policy,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data.get("is_default", False):
            LoanProduct.objects.filter(
                chama_id=loan_policy.chama_id, is_default=True
            ).exclude(id=loan_policy.id).update(is_default=False)
        for field, value in serializer.validated_data.items():
            if field == "chama_id":
                continue
            setattr(loan_policy, field, value)
        loan_policy.updated_by = request.user
        loan_policy.save()

        return Response(LoanProductSerializer(loan_policy).data)


class ContributionTypeListCreateView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def get(self, request):
        chama_id = _resolve_chama_id(
            request,
            request.query_params.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)

        queryset = ContributionType.objects.filter(chama_id=chama_id).order_by("name")
        serializer = ContributionTypeSerializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = ContributionTypeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        chama_id = _validate_uuid(serializer.validated_data.get("chama_id"), "chama_id")
        _require_roles(
            request.user,
            chama_id,
            {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER},
            "Only treasurer or admin can manage contribution types.",
        )

        contribution_type = ContributionType.objects.create(
            chama_id=chama_id,
            name=serializer.validated_data["name"],
            frequency=serializer.validated_data["frequency"],
            default_amount=serializer.validated_data["default_amount"],
            is_active=serializer.validated_data.get("is_active", True),
            created_by=request.user,
            updated_by=request.user,
        )

        return Response(
            ContributionTypeSerializer(contribution_type).data,
            status=status.HTTP_201_CREATED,
        )


class ContributionTypeDetailView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def get_object(self, type_id):
        return ContributionType.objects.filter(id=type_id).first()

    def get(self, request, id):
        contribution_type = self.get_object(id)
        if not contribution_type:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_membership(request.user, str(contribution_type.chama_id))
        return Response(ContributionTypeSerializer(contribution_type).data)

    def patch(self, request, id):
        contribution_type = self.get_object(id)
        if not contribution_type:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_roles(
            request.user,
            str(contribution_type.chama_id),
            {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER},
            "Only treasurer or admin can manage contribution types.",
        )

        serializer = ContributionTypeSerializer(
            contribution_type,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            if field == "chama_id":
                continue
            setattr(contribution_type, field, value)
        contribution_type.updated_by = request.user
        contribution_type.save()

        return Response(ContributionTypeSerializer(contribution_type).data)

    def delete(self, request, id):
        contribution_type = self.get_object(id)
        if not contribution_type:
            return Response(status=status.HTTP_204_NO_CONTENT)

        _require_roles(
            request.user,
            str(contribution_type.chama_id),
            {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER},
            "Only treasurer or admin can manage contribution types.",
        )
        contribution_type.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ContributionGoalListCreateView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def get(self, request):
        query_payload = request.query_params.copy()
        query_payload.setdefault(
            "chama_id",
            request.headers.get("X-CHAMA-ID")
            or request.session.get("active_chama_id"),
        )
        query = WalletQuerySerializer(data=query_payload)
        query.is_valid(raise_exception=True)
        chama_id = _resolve_chama_id(
            request,
            query.validated_data.get("chama_id"),
        )
        membership = _require_membership(request.user, chama_id)
        member_id = str(query.validated_data.get("member_id") or request.user.id)
        effective_role = get_effective_role(request.user, chama_id, membership)
        if effective_role == MembershipRole.MEMBER and member_id != str(request.user.id):
            raise PermissionDenied("Members can only view their own goals.")

        queryset = ContributionGoal.objects.filter(
            chama_id=chama_id,
            member_id=member_id,
        ).order_by("-created_at")
        return Response(ContributionGoalSerializer(queryset, many=True).data)

    def post(self, request):
        serializer = ContributionGoalUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        chama_id = str(payload["chama_id"])
        membership = _require_membership(request.user, chama_id)
        member_id = str(payload.get("member_id") or request.user.id)
        effective_role = get_effective_role(request.user, chama_id, membership)
        if effective_role == MembershipRole.AUDITOR:
            raise PermissionDenied("Auditor role is read-only.")
        if effective_role == MembershipRole.MEMBER and member_id != str(request.user.id):
            raise PermissionDenied("Members can only manage their own goals.")
        payload["member_id"] = member_id

        try:
            goal = FinanceService.upsert_contribution_goal(payload, request.user)
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)

        return Response(ContributionGoalSerializer(goal).data, status=status.HTTP_201_CREATED)


class ContributionGoalDetailView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def _get_goal(self, id):
        return ContributionGoal.objects.select_related("member", "chama").filter(id=id).first()

    def get(self, request, id):
        goal = self._get_goal(id)
        if not goal:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        membership = _require_membership(request.user, str(goal.chama_id))
        effective_role = get_effective_role(request.user, str(goal.chama_id), membership)
        if effective_role == MembershipRole.MEMBER and goal.member_id != request.user.id:
            raise PermissionDenied("Members can only view their own goals.")

        return Response(ContributionGoalSerializer(goal).data)

    def patch(self, request, id):
        goal = self._get_goal(id)
        if not goal:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        membership = _require_membership(request.user, str(goal.chama_id))
        effective_role = get_effective_role(request.user, str(goal.chama_id), membership)
        if effective_role == MembershipRole.AUDITOR:
            raise PermissionDenied("Auditor role is read-only.")
        if effective_role == MembershipRole.MEMBER and goal.member_id != request.user.id:
            raise PermissionDenied("Members can only update their own goals.")

        serializer = ContributionGoalUpsertSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        if "chama_id" in payload and str(payload["chama_id"]) != str(goal.chama_id):
            raise ValidationError({"chama_id": "chama_id cannot be changed."})
        if "member_id" in payload and str(payload["member_id"]) != str(goal.member_id):
            raise ValidationError({"member_id": "member_id cannot be changed."})

        for field, value in payload.items():
            if field in {"chama_id", "member_id"}:
                continue
            setattr(goal, field, value)

        if "status" in payload:
            goal.is_active = goal.status == ContributionGoalStatus.ACTIVE
        goal.updated_by = request.user
        goal.save()

        return Response(ContributionGoalSerializer(goal).data)

    def delete(self, request, id):
        goal = self._get_goal(id)
        if not goal:
            return Response(status=status.HTTP_204_NO_CONTENT)

        membership = _require_membership(request.user, str(goal.chama_id))
        effective_role = get_effective_role(request.user, str(goal.chama_id), membership)
        if effective_role == MembershipRole.AUDITOR:
            raise PermissionDenied("Auditor role is read-only.")
        if effective_role == MembershipRole.MEMBER and goal.member_id != request.user.id:
            raise PermissionDenied("Members can only delete their own goals.")

        goal.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ContributionListView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def get(self, request):
        query_payload = request.query_params.copy()
        query_payload.setdefault(
            "chama_id",
            request.headers.get("X-CHAMA-ID")
            or request.session.get("active_chama_id"),
        )
        query = WalletQuerySerializer(data=query_payload)
        query.is_valid(raise_exception=True)

        chama_id = _resolve_chama_id(
            request,
            query.validated_data.get("chama_id"),
        )
        membership = _require_membership(request.user, chama_id)
        member_id = str(query.validated_data.get("member_id") or request.user.id)
        effective_role = get_effective_role(request.user, chama_id, membership)
        if effective_role == MembershipRole.MEMBER and member_id != str(request.user.id):
            raise PermissionDenied("Members can only view their own contributions.")
        if effective_role == MembershipRole.AUDITOR and member_id != str(request.user.id):
            raise PermissionDenied("Auditor cannot view member contributions directly.")

        queryset = (
            Contribution.objects.select_related(
                "member",
                "recorded_by",
                "contribution_type",
            )
            .filter(
                chama_id=chama_id,
                member_id=member_id,
            )
            .order_by("-date_paid", "-created_at")
        )
        return Response(ContributionSerializer(queryset, many=True).data)


class WalletBalanceView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def get(self, request):
        query_payload = request.query_params.copy()
        query_payload.setdefault(
            "chama_id",
            request.headers.get("X-CHAMA-ID")
            or request.session.get("active_chama_id"),
        )
        query = WalletQuerySerializer(data=query_payload)
        query.is_valid(raise_exception=True)
        chama_id = _resolve_chama_id(
            request,
            query.validated_data.get("chama_id"),
        )
        membership = _require_membership(request.user, chama_id)
        member_id = str(query.validated_data.get("member_id") or request.user.id)
        effective_role = get_effective_role(request.user, chama_id, membership)
        if effective_role == MembershipRole.MEMBER and member_id != str(request.user.id):
            raise PermissionDenied("Members can only view their own wallet.")
        if effective_role == MembershipRole.AUDITOR and member_id != str(request.user.id):
            raise PermissionDenied("Auditor cannot view member wallets directly.")

        return Response(FinanceService.compute_wallet_balance(chama_id, member_id))


class WalletSummaryView(FinanceBaseView):
    """Get comprehensive wallet summary with today/month aggregates and pending counts"""
    billing_feature_key = "contributions_basic"
    
    def get(self, request):
        query_payload = request.query_params.copy()
        query_payload.setdefault(
            "chama_id",
            request.headers.get("X-CHAMA-ID")
            or request.session.get("active_chama_id"),
        )
        query = WalletQuerySerializer(data=query_payload)
        query.is_valid(raise_exception=True)
        chama_id = _resolve_chama_id(
            request,
            query.validated_data.get("chama_id"),
        )
        membership = _require_membership(request.user, chama_id)
        member_id = str(query.validated_data.get("member_id") or request.user.id)
        effective_role = get_effective_role(request.user, chama_id, membership)
        
        if effective_role == MembershipRole.MEMBER and member_id != str(request.user.id):
            raise PermissionDenied("Members can only view their own wallet summary.")
        if effective_role == MembershipRole.AUDITOR and member_id != str(request.user.id):
            raise PermissionDenied("Auditor cannot view member wallet summary directly.")
        
        # Get wallet balance
        wallet_data = FinanceService.compute_wallet_balance(chama_id, member_id)
        
        # Get ledger entries for today and this month
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # Aggregate inflow/outflow by date range
        inflow_today = Decimal("0.00")
        inflow_this_month = Decimal("0.00")
        outflow_today = Decimal("0.00")
        outflow_this_month = Decimal("0.00")
        last_transaction_date = None
        
        ledger_today = LedgerEntry.objects.filter(
            chama_id=chama_id,
            created_at__gte=today_start,
        )
        
        ledger_month = LedgerEntry.objects.filter(
            chama_id=chama_id,
            created_at__gte=month_start,
        )
        
        # Sum inflows/outflows for today
        for entry in ledger_today:
            if entry.direction == "CREDIT":
                inflow_today += entry.amount
            else:
                outflow_today += entry.amount
        
        # Sum inflows/outflows for month
        for entry in ledger_month:
            if entry.direction == "CREDIT":
                inflow_this_month += entry.amount
            else:
                outflow_this_month += entry.amount
            last_transaction_date = entry.created_at
        
        # Count pending payments (from payments app)
        from apps.payments.models import PaymentIntent, PaymentIntentStatus
        
        pending_deposits = PaymentIntent.objects.filter(
            member_id=member_id,
            chama_id=chama_id,
            purpose="DEPOSIT",
            status__in=[PaymentIntentStatus.PENDING, PaymentIntentStatus.PENDING_CALLBACK],
        ).count()
        
        pending_withdrawals = PaymentIntent.objects.filter(
            member_id=member_id,
            chama_id=chama_id,
            purpose="WITHDRAWAL",
            status__in=[PaymentIntentStatus.PENDING, PaymentIntentStatus.PENDING_CALLBACK],
        ).count()
        
        # Prepare summary response
        summary_data = {
            "chama_id": wallet_data.get("chama_id"),
            "member_id": wallet_data.get("member_id"),
            "currency": wallet_data.get("currency"),
            "available_balance": wallet_data.get("wallet_balance", "0.00"),
            "pending_balance": Decimal("0.00"),  # Could be calculated based on pending payments
            "inflow_today": str(inflow_today),
            "inflow_this_month": str(inflow_this_month),
            "outflow_today": str(outflow_today),
            "outflow_this_month": str(outflow_this_month),
            "pending_deposits": pending_deposits,
            "pending_withdrawals": pending_withdrawals,
            "last_transaction_date": last_transaction_date,
        }
        
        serializer = WalletSummarySerializer(summary_data)
        return Response(serializer.data)


class CreditScoreView(FinanceBaseView):
    def get(self, request):
        query = CreditScoreQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        chama_id = str(query.validated_data["chama_id"])
        membership = _require_membership(request.user, chama_id)
        member_id = str(query.validated_data.get("member_id") or request.user.id)
        effective_role = get_effective_role(request.user, chama_id, membership)
        if effective_role == MembershipRole.MEMBER and member_id != str(request.user.id):
            raise PermissionDenied("Members can only view their own credit score.")
        if effective_role == MembershipRole.AUDITOR and member_id != str(request.user.id):
            raise PermissionDenied("Auditor cannot view member credit scores directly.")

        return Response(FinanceService.compute_credit_score(chama_id, member_id))


class LoanGuarantorListCreateView(FinanceBaseView):
    def get(self, request, id):
        loan = Loan.objects.filter(id=id).first()
        if not loan:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        membership = _require_membership(request.user, str(loan.chama_id))
        effective_role = get_effective_role(request.user, str(loan.chama_id), membership)
        if effective_role == MembershipRole.MEMBER and loan.member_id != request.user.id:
            raise PermissionDenied("Members can only view guarantors for their own loan.")

        queryset = LoanGuarantor.objects.filter(loan=loan).order_by("created_at")
        return Response(LoanGuarantorSerializer(queryset, many=True).data)

    def post(self, request, id):
        loan = Loan.objects.filter(id=id).first()
        if not loan:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        membership = _require_membership(request.user, str(loan.chama_id))
        effective_role = get_effective_role(request.user, str(loan.chama_id), membership)
        if effective_role == MembershipRole.MEMBER and loan.member_id != request.user.id:
            raise PermissionDenied("Members can only add guarantors for their own loan.")
        if effective_role == MembershipRole.AUDITOR:
            raise PermissionDenied("Auditor role is read-only.")

        serializer = LoanGuarantorCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        if str(payload["loan_id"]) != str(id):
            raise ValidationError({"loan_id": "loan_id must match URL loan id."})

        try:
            guarantor = FinanceService.add_loan_guarantor(payload, request.user)
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)

        return Response(LoanGuarantorSerializer(guarantor).data, status=status.HTTP_201_CREATED)


class LoanGuarantorRespondView(FinanceBaseView):
    def post(self, request, id):
        guarantor_record = LoanGuarantor.objects.select_related("loan").filter(id=id).first()
        if not guarantor_record:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        _require_membership(request.user, str(guarantor_record.loan.chama_id))
        serializer = LoanGuarantorActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            record = FinanceService.respond_to_loan_guarantor(
                id,
                actor=request.user,
                decision=serializer.validated_data["decision"],
                note=serializer.validated_data.get("note", ""),
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(LoanGuarantorSerializer(record).data)


class ContributionRecordView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def post(self, request):
        serializer = ContributionRecordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        chama_id = str(payload["chama_id"])
        _require_roles(
            request.user,
            chama_id,
            {MembershipRole.TREASURER, MembershipRole.CHAMA_ADMIN},
            "Only treasurer or admin can record contributions.",
        )

        try:
            result = FinanceService.post_contribution(payload, request.user)
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)

        return Response(
            {
                "contribution": ContributionSerializer(result.created).data,
                "ledger_entry": LedgerEntrySerializer(result.ledger_entry).data,
            },
            status=status.HTTP_201_CREATED,
        )


class LoanEligibilityView(FinanceBaseView):
    def get(self, request):
        serializer = LoanEligibilitySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return self._run_eligibility_check(request, serializer.validated_data)

    def post(self, request):
        serializer = LoanEligibilitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._run_eligibility_check(request, serializer.validated_data)

    def _run_eligibility_check(self, request, payload):
        chama_id = str(payload["chama_id"])
        membership = _require_membership(request.user, chama_id)
        member_id = str(payload.get("member_id") or request.user.id)
        effective_role = get_effective_role(request.user, chama_id, membership)
        if member_id != str(request.user.id) and effective_role not in {
            MembershipRole.TREASURER,
            MembershipRole.CHAMA_ADMIN,
        }:
            raise PermissionDenied(
                "Members can only run eligibility checks for themselves."
            )

        payload["member_id"] = member_id
        try:
            return Response(
                FinanceService.check_loan_eligibility(payload, request.user)
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)


class LoanApplicationListCreateView(FinanceBaseView):
    def get(self, request):
        chama_id = _resolve_chama_id(request, request.query_params.get("chama_id"))
        membership = _require_membership(request.user, chama_id)
        effective_role = get_effective_role(request.user, chama_id, membership)

        queryset = (
            LoanApplication.objects.select_related(
                "member",
                "loan_product",
                "reviewed_by",
                "approved_by",
                "created_loan",
            )
            .prefetch_related("approval_logs", "guarantors")
            .filter(chama_id=chama_id)
            .order_by("-submitted_at")
        )
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if effective_role == MembershipRole.MEMBER:
            queryset = queryset.filter(member=request.user)
        return Response(LoanApplicationSerializer(queryset, many=True).data)

    def post(self, request):
        serializer = LoanApplicationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        chama_id = str(payload["chama_id"])
        membership = _require_membership(request.user, chama_id)

        requested_member_id = str(payload.get("member_id") or request.user.id)
        effective_role = get_effective_role(request.user, chama_id, membership)
        if requested_member_id != str(request.user.id) and effective_role not in {
            MembershipRole.TREASURER,
            MembershipRole.CHAMA_ADMIN,
        }:
            raise PermissionDenied("Members can only request loans for themselves.")

        payload["member_id"] = requested_member_id

        eligibility_payload = {
            "chama_id": payload["chama_id"],
            "member_id": payload["member_id"],
            "loan_product_id": payload.get("loan_product_id"),
            "principal": payload["requested_amount"],
            "duration_months": payload["requested_term_months"],
            "purpose": payload.get("purpose", ""),
        }
        try:
            eligibility = FinanceService.check_loan_eligibility(
                eligibility_payload,
                request.user,
            )
            if not eligibility["eligible"]:
                return Response(
                    {
                        "code": "LOAN_NOT_ELIGIBLE",
                        "message": "Loan application failed eligibility checks.",
                        "detail": "Loan application failed eligibility checks.",
                        **eligibility,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            application = FinanceService.request_loan_application(payload, request.user)
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)

        return Response(
            LoanApplicationSerializer(application).data,
            status=status.HTTP_201_CREATED,
        )


class LoanRequestView(FinanceBaseView):
    def post(self, request):
        serializer = LoanRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application_payload = {
            "chama_id": serializer.validated_data["chama_id"],
            "member_id": serializer.validated_data.get("member_id"),
            "loan_product_id": serializer.validated_data.get("loan_product_id"),
            "requested_amount": serializer.validated_data["principal"],
            "requested_term_months": serializer.validated_data["duration_months"],
            "purpose": serializer.validated_data.get("purpose", ""),
            "guarantors": serializer.validated_data.get("guarantors") or [],
        }

        chama_id = str(application_payload["chama_id"])
        membership = _require_membership(request.user, chama_id)
        requested_member_id = str(application_payload.get("member_id") or request.user.id)
        effective_role = get_effective_role(request.user, chama_id, membership)
        if requested_member_id != str(request.user.id) and effective_role not in {
            MembershipRole.TREASURER,
            MembershipRole.CHAMA_ADMIN,
        }:
            raise PermissionDenied("Members can only request loans for themselves.")
        application_payload["member_id"] = requested_member_id

        try:
            eligibility = FinanceService.check_loan_eligibility(
                {
                    "chama_id": application_payload["chama_id"],
                    "member_id": application_payload["member_id"],
                    "loan_product_id": application_payload.get("loan_product_id"),
                    "principal": application_payload["requested_amount"],
                    "duration_months": application_payload["requested_term_months"],
                    "purpose": application_payload.get("purpose", ""),
                },
                request.user,
            )
            if not eligibility["eligible"]:
                return Response(
                    {
                        "code": "LOAN_NOT_ELIGIBLE",
                        "message": "Loan application failed eligibility checks.",
                        "detail": "Loan application failed eligibility checks.",
                        **eligibility,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            application = FinanceService.request_loan_application(
                application_payload,
                request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(
            LoanApplicationSerializer(application).data,
            status=status.HTTP_201_CREATED,
        )


class LoanListView(FinanceBaseView):
    def get(self, request):
        chama_id = _resolve_chama_id(request, request.query_params.get("chama_id"))
        membership = _require_membership(request.user, chama_id)

        queryset = (
            Loan.objects.select_related("member", "loan_product")
            .prefetch_related("approval_logs")
            .filter(chama_id=chama_id)
            .order_by("-requested_at")
        )
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        effective_role = get_effective_role(request.user, chama_id, membership)
        if effective_role == MembershipRole.MEMBER:
            queryset = queryset.filter(member=request.user)

        return Response(LoanSerializer(queryset, many=True).data)


class LoanApplicationReviewView(FinanceBaseView):
    def post(self, request, id):
        application = LoanApplication.objects.filter(id=id).first()
        if not application:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_roles(
            request.user,
            str(application.chama_id),
            {MembershipRole.TREASURER},
            "Only treasurer can review loan applications.",
        )
        serializer = LoanApplicationDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            application = FinanceService.review_loan_application(
                id,
                actor=request.user,
                decision=serializer.validated_data["decision"],
                note=serializer.validated_data.get("note", ""),
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(LoanApplicationSerializer(application).data)


class LoanApplicationCommitteeApproveView(FinanceBaseView):
    def post(self, request, id):
        application = LoanApplication.objects.filter(id=id).first()
        if not application:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_roles(
            request.user,
            str(application.chama_id),
            {MembershipRole.SECRETARY, MembershipRole.CHAMA_ADMIN},
            "Only secretary or chama admin can record committee decisions.",
        )
        serializer = LoanApplicationDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            application = FinanceService.committee_approve_loan_application(
                id,
                actor=request.user,
                decision=serializer.validated_data["decision"],
                note=serializer.validated_data.get("note", ""),
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(LoanApplicationSerializer(application).data)


class LoanApplicationApproveView(FinanceBaseView):
    def post(self, request, id):
        application = LoanApplication.objects.filter(id=id).first()
        if not application:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_roles(
            request.user,
            str(application.chama_id),
            {MembershipRole.CHAMA_ADMIN},
            "Only chama admin can approve loan applications.",
        )
        try:
            application = FinanceService.approve_loan_application(
                id,
                actor=request.user,
                note=request.data.get("note", ""),
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(LoanApplicationSerializer(application).data)


class LoanApplicationRejectView(FinanceBaseView):
    def post(self, request, id):
        application = LoanApplication.objects.filter(id=id).first()
        if not application:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_roles(
            request.user,
            str(application.chama_id),
            {MembershipRole.CHAMA_ADMIN},
            "Only chama admin can reject loan applications.",
        )
        try:
            application = FinanceService.reject_loan_application(
                id,
                actor=request.user,
                note=request.data.get("note", ""),
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(LoanApplicationSerializer(application).data)


class LoanApplicationDisburseView(FinanceBaseView):
    def post(self, request, id):
        application = LoanApplication.objects.filter(id=id).first()
        if not application:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_roles(
            request.user,
            str(application.chama_id),
            {MembershipRole.CHAMA_ADMIN},
            "Only chama admin can disburse approved loan applications.",
        )
        idempotency_key = (
            request.data.get("idempotency_key")
            if hasattr(request.data, "get")
            else None
        )
        try:
            application, result = FinanceService.disburse_loan_application(
                id,
                actor=request.user,
                idempotency_key=idempotency_key,
                disbursement_reference=request.data.get("disbursement_reference", ""),
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(
            {
                "application": LoanApplicationSerializer(application).data,
                "loan": LoanSerializer(result.created).data,
                "ledger_entry": LedgerEntrySerializer(result.ledger_entry).data,
            }
        )


class LoanApplicationApprovalLogListView(FinanceBaseView):
    def get(self, request, id):
        application = LoanApplication.objects.filter(id=id).first()
        if not application:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        membership = _require_membership(request.user, str(application.chama_id))
        effective_role = get_effective_role(
            request.user, str(application.chama_id), membership
        )
        if effective_role == MembershipRole.MEMBER and application.member_id != request.user.id:
            raise PermissionDenied(
                "Members can only view their own loan application approval history."
            )
        logs = LoanApplicationApproval.objects.filter(
            loan_application=application
        ).order_by("acted_at", "created_at")
        return Response(LoanApplicationApprovalSerializer(logs, many=True).data)


class LoanApplicationGuarantorRespondView(FinanceBaseView):
    def post(self, request, id):
        guarantor_record = LoanApplicationGuarantor.objects.select_related(
            "loan_application"
        ).filter(id=id).first()
        if not guarantor_record:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        _require_membership(request.user, str(guarantor_record.loan_application.chama_id))
        serializer = LoanApplicationGuarantorActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            record = FinanceService.respond_to_loan_application_guarantor(
                id,
                actor=request.user,
                decision=serializer.validated_data["decision"],
                note=serializer.validated_data.get("note", ""),
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(LoanApplicationGuarantorSerializer(record).data)


class LoanApplicationGuarantorListCreateView(FinanceBaseView):
    def get(self, request, id):
        application = LoanApplication.objects.filter(id=id).first()
        if not application:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        membership = _require_membership(request.user, str(application.chama_id))
        effective_role = get_effective_role(
            request.user, str(application.chama_id), membership
        )
        if effective_role == MembershipRole.MEMBER and application.member_id != request.user.id:
            raise PermissionDenied(
                "Members can only view guarantors for their own loan application."
            )
        queryset = LoanApplicationGuarantor.objects.filter(
            loan_application=application
        ).order_by("created_at", "id")
        return Response(LoanApplicationGuarantorSerializer(queryset, many=True).data)

    def post(self, request, id):
        application = LoanApplication.objects.filter(id=id).first()
        if not application:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        membership = _require_membership(request.user, str(application.chama_id))
        effective_role = get_effective_role(
            request.user, str(application.chama_id), membership
        )
        if effective_role == MembershipRole.MEMBER and application.member_id != request.user.id:
            raise PermissionDenied(
                "Members can only add guarantors to their own loan application."
            )

        serializer = LoanApplicationGuarantorCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        payload["loan_application_id"] = str(application.id)
        try:
            guarantor = FinanceService.add_loan_application_guarantor(payload, request.user)
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(
            LoanApplicationGuarantorSerializer(guarantor).data,
            status=status.HTTP_201_CREATED,
        )


class LoanReviewView(FinanceBaseView):
    def post(self, request, id):
        loan = Loan.objects.filter(id=id).first()
        if not loan:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_roles(
            request.user,
            str(loan.chama_id),
            {MembershipRole.TREASURER},
            "Only treasurer can review loans.",
        )
        serializer = LoanReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            loan = FinanceService.review_loan(
                id,
                request.user,
                serializer.validated_data["decision"],
                serializer.validated_data.get("note", ""),
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(LoanSerializer(loan).data)


class LoanApproveView(FinanceBaseView):
    def post(self, request, id):
        loan = Loan.objects.filter(id=id).first()
        if not loan:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_roles(
            request.user,
            str(loan.chama_id),
            {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER},
            "Only chama admin or treasurer can approve loans.",
        )

        try:
            loan = FinanceService.approve_loan(
                id, request.user, request.data.get("note", "")
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)

        return Response(LoanSerializer(loan).data)


class LoanRejectView(FinanceBaseView):
    def post(self, request, id):
        loan = Loan.objects.filter(id=id).first()
        if not loan:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_roles(
            request.user,
            str(loan.chama_id),
            {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER},
            "Only admin or treasurer can reject loans.",
        )

        try:
            if (
                get_effective_role(
                    request.user,
                    str(loan.chama_id),
                    get_chama_membership(request.user, str(loan.chama_id)),
                )
                == MembershipRole.TREASURER
            ):
                loan = FinanceService.review_loan(
                    id,
                    request.user,
                    "rejected",
                    request.data.get("note", ""),
                )
            else:
                loan = FinanceService.reject_loan(
                    id, request.user, request.data.get("note", "")
                )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)

        return Response(LoanSerializer(loan).data)


class LoanDisburseView(FinanceBaseView):
    def post(self, request, id):
        loan = Loan.objects.filter(id=id).first()
        if not loan:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_roles(
            request.user,
            str(loan.chama_id),
            {MembershipRole.CHAMA_ADMIN},
            "Only chama admin can disburse loans.",
        )

        idempotency_key = (
            request.data.get("idempotency_key")
            if hasattr(request.data, "get")
            else None
        )
        try:
            result = FinanceService.disburse_loan(
                id,
                request.user,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)

        return Response(
            {
                "loan": LoanSerializer(result.created).data,
                "ledger_entry": LedgerEntrySerializer(result.ledger_entry).data,
            }
        )


class LoanRepayView(FinanceBaseView):
    def post(self, request, id):
        loan = Loan.objects.filter(id=id).first()
        if not loan:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_roles(
            request.user,
            str(loan.chama_id),
            {MembershipRole.TREASURER, MembershipRole.CHAMA_ADMIN},
            "Only treasurer or admin can post repayments.",
        )

        serializer = RepaymentPostSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = FinanceService.post_repayment(
                id, serializer.validated_data, request.user
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)

        return Response(
            {
                "repayment": RepaymentSerializer(result.created).data,
                "ledger_entry": LedgerEntrySerializer(result.ledger_entry).data,
            },
            status=status.HTTP_201_CREATED,
        )


class LoanTopUpRequestView(FinanceBaseView):
    def post(self, request, id):
        loan = Loan.objects.filter(id=id).first()
        if not loan:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        membership = _require_membership(request.user, str(loan.chama_id))
        effective_role = get_effective_role(request.user, str(loan.chama_id), membership)
        if effective_role == MembershipRole.MEMBER and loan.member_id != request.user.id:
            raise PermissionDenied("Members can only request top-up for their own loan.")
        if effective_role == MembershipRole.AUDITOR:
            raise PermissionDenied("Auditor role is read-only.")

        serializer = LoanTopUpRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            topup = FinanceService.request_loan_topup(id, serializer.validated_data, request.user)
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(LoanTopUpRequestSerializer(topup).data, status=status.HTTP_201_CREATED)


class LoanTopUpReviewView(FinanceBaseView):
    def post(self, request, id):
        topup = LoanTopUpRequest.objects.select_related("loan").filter(id=id).first()
        if not topup:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        _require_roles(
            request.user,
            str(topup.loan.chama_id),
            {MembershipRole.CHAMA_ADMIN},
            "Only chama admin can review top-up requests.",
        )
        serializer = LoanTopUpReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            reviewed = FinanceService.review_loan_topup(
                id,
                actor=request.user,
                decision=serializer.validated_data["decision"],
                note=serializer.validated_data.get("note", ""),
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(LoanTopUpRequestSerializer(reviewed).data)


class LoanRestructureRequestView(FinanceBaseView):
    def post(self, request, id):
        loan = Loan.objects.filter(id=id).first()
        if not loan:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        membership = _require_membership(request.user, str(loan.chama_id))
        effective_role = get_effective_role(request.user, str(loan.chama_id), membership)
        if effective_role == MembershipRole.MEMBER and loan.member_id != request.user.id:
            raise PermissionDenied("Members can only request restructure for own loan.")
        if effective_role == MembershipRole.AUDITOR:
            raise PermissionDenied("Auditor role is read-only.")

        serializer = LoanRestructureRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            request_obj = FinanceService.request_loan_restructure(
                id,
                serializer.validated_data,
                request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(
            LoanRestructureRequestSerializer(request_obj).data,
            status=status.HTTP_201_CREATED,
        )


class LoanRestructureRequestListView(FinanceBaseView):
    def get(self, request):
        chama_id = _resolve_chama_id(
            request,
            request.query_params.get("chama_id"),
            label="chama_id",
        )
        _require_roles(
            request.user,
            chama_id,
            {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER, MembershipRole.AUDITOR},
            "Only auditor, treasurer, or admin can view restructure requests.",
        )

        queryset = LoanRestructureRequest.objects.select_related("loan", "reviewed_by").filter(
            loan__chama_id=chama_id
        )
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return Response(LoanRestructureRequestSerializer(queryset.order_by("-created_at"), many=True).data)


class LoanRestructureReviewView(FinanceBaseView):
    def post(self, request, id):
        request_obj = LoanRestructureRequest.objects.select_related("loan").filter(id=id).first()
        if not request_obj:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        _require_roles(
            request.user,
            str(request_obj.loan.chama_id),
            {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER},
            "Only treasurer/admin can review restructure requests.",
        )
        serializer = LoanRestructureReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            reviewed = FinanceService.review_loan_restructure(
                id,
                actor=request.user,
                decision=serializer.validated_data["decision"],
                note=serializer.validated_data.get("note", ""),
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(LoanRestructureRequestSerializer(reviewed).data)


class LoanRecoveryActionListCreateView(FinanceBaseView):
    def get(self, request, id):
        loan = Loan.objects.filter(id=id).first()
        if not loan:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        membership = _require_membership(request.user, str(loan.chama_id))
        effective_role = get_effective_role(request.user, str(loan.chama_id), membership)
        if effective_role == MembershipRole.MEMBER and loan.member_id != request.user.id:
            raise PermissionDenied("Members can only view recovery history for their own loans.")
        queryset = LoanRecoveryAction.objects.filter(loan=loan).order_by("-created_at")
        return Response(LoanRecoveryActionSerializer(queryset, many=True).data)

    def post(self, request, id):
        loan = Loan.objects.filter(id=id).first()
        if not loan:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        _require_roles(
            request.user,
            str(loan.chama_id),
            {MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER},
            "Only treasurer or admin can record recovery actions.",
        )
        serializer = LoanRecoveryActionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            action = FinanceService.record_recovery_action(id, serializer.validated_data, request.user)
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(LoanRecoveryActionSerializer(action).data, status=status.HTTP_201_CREATED)


class LoanOffsetFromSavingsView(FinanceBaseView):
    def post(self, request, id):
        loan = Loan.objects.filter(id=id).first()
        if not loan:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        _require_roles(
            request.user,
            str(loan.chama_id),
            {MembershipRole.CHAMA_ADMIN},
            "Only chama admin can offset loans from savings.",
        )
        serializer = LoanOffsetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = FinanceService.offset_loan_from_savings(id, serializer.validated_data, request.user)
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(
            {
                "repayment": RepaymentSerializer(result.created).data,
                "ledger_entry": LedgerEntrySerializer(result.ledger_entry).data,
            },
            status=status.HTTP_201_CREATED,
        )


class LoanWriteOffView(FinanceBaseView):
    def post(self, request, id):
        loan = Loan.objects.filter(id=id).first()
        if not loan:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        _require_roles(
            request.user,
            str(loan.chama_id),
            {MembershipRole.CHAMA_ADMIN},
            "Only chama admin can write off loans.",
        )
        serializer = LoanWriteOffSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            action = FinanceService.write_off_loan(id, serializer.validated_data, request.user)
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(LoanRecoveryActionSerializer(action).data, status=status.HTTP_201_CREATED)


class LoanScheduleView(FinanceBaseView):
    def get(self, request, id):
        loan = Loan.objects.filter(id=id).first()
        if not loan:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        membership = _require_membership(request.user, str(loan.chama_id))
        effective_role = get_effective_role(request.user, str(loan.chama_id), membership)
        if (
            effective_role == MembershipRole.MEMBER
            and loan.member_id != request.user.id
        ):
            raise PermissionDenied("Members can only view their own loan schedules.")

        schedule = InstallmentSchedule.objects.filter(loan=loan).order_by(
            "due_date", "created_at"
        )
        return Response(InstallmentScheduleSerializer(schedule, many=True).data)


class LoanNextDueView(FinanceBaseView):
    def get(self, request, id):
        loan = Loan.objects.filter(id=id).first()
        if not loan:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        membership = _require_membership(request.user, str(loan.chama_id))
        effective_role = get_effective_role(request.user, str(loan.chama_id), membership)
        if (
            effective_role == MembershipRole.MEMBER
            and loan.member_id != request.user.id
        ):
            raise PermissionDenied("Members can only view their own next due.")

        return Response(FinanceService.get_next_due_installment(id))


class LoanApprovalLogListView(FinanceBaseView):
    def get(self, request, id):
        loan = Loan.objects.filter(id=id).first()
        if not loan:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        membership = _require_membership(request.user, str(loan.chama_id))
        effective_role = get_effective_role(request.user, str(loan.chama_id), membership)
        if (
            effective_role == MembershipRole.MEMBER
            and loan.member_id != request.user.id
        ):
            raise PermissionDenied("Members can only view their own approval history.")

        logs = LoanApprovalLog.objects.filter(loan=loan).order_by("acted_at")
        return Response(LoanApprovalLogSerializer(logs, many=True).data)


class PenaltyIssueView(FinanceBaseView):
    def post(self, request):
        serializer = PenaltyIssueSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        chama_id = str(payload["chama_id"])
        _require_roles(
            request.user,
            chama_id,
            {MembershipRole.TREASURER, MembershipRole.CHAMA_ADMIN},
            "Only treasurer or admin can issue penalties.",
        )

        try:
            result = FinanceService.issue_penalty(payload, request.user)
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)

        return Response(
            {
                "penalty": PenaltySerializer(result.created).data,
                "ledger_entry": LedgerEntrySerializer(result.ledger_entry).data,
            },
            status=status.HTTP_201_CREATED,
        )


class PenaltyMarkPaidView(FinanceBaseView):
    def post(self, request, id):
        penalty = Penalty.objects.filter(id=id).first()
        if not penalty:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_roles(
            request.user,
            str(penalty.chama_id),
            {MembershipRole.TREASURER, MembershipRole.CHAMA_ADMIN},
            "Only treasurer or admin can mark penalties paid.",
        )

        serializer = IdempotencyOnlySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = FinanceService.mark_penalty_paid(
                id, serializer.validated_data, request.user
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)

        return Response(
            {
                "penalty": PenaltySerializer(result.created).data,
                "ledger_entry": LedgerEntrySerializer(result.ledger_entry).data,
            }
        )


class PenaltyWaiveView(FinanceBaseView):
    def post(self, request, id):
        penalty = Penalty.objects.filter(id=id).first()
        if not penalty:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_roles(
            request.user,
            str(penalty.chama_id),
            {MembershipRole.CHAMA_ADMIN},
            "Only chama admin can waive penalties.",
        )

        try:
            result = FinanceService.waive_penalty(id, request.user)
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)

        return Response(
            {
                "penalty": PenaltySerializer(result.created).data,
                "ledger_entry": LedgerEntrySerializer(result.ledger_entry).data,
            }
        )


class LedgerView(FinanceBaseView):
    def get(self, request):
        query = LedgerQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)

        chama_id = str(query.validated_data["chama_id"])
        _require_roles(
            request.user,
            chama_id,
            {
                MembershipRole.AUDITOR,
                MembershipRole.TREASURER,
                MembershipRole.SECRETARY,
                MembershipRole.CHAMA_ADMIN,
            },
            "Only auditor or higher can view chama ledger.",
        )

        queryset = LedgerEntry.objects.filter(chama_id=chama_id)
        from_date = query.validated_data.get("from")
        to_date = query.validated_data.get("to")

        if from_date:
            queryset = queryset.filter(created_at__date__gte=from_date)
        if to_date:
            queryset = queryset.filter(created_at__date__lte=to_date)

        return Response(LedgerEntrySerializer(queryset, many=True).data)


class AllTransactionsView(FinanceBaseView):
    """
    Chama-wide unified transactions feed.

    Implements the "All Transactions" workflow feed by aggregating:
    - JournalEntry business postings (double-entry)
    - Standalone ledger entries (non-journal postings)
    - Pending/unposted payment intents
    """

    def get(self, request):
        query = AllTransactionsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)

        chama_id = str(query.validated_data["chama_id"])
        _require_roles(
            request.user,
            chama_id,
            {
                MembershipRole.AUDITOR,
                MembershipRole.TREASURER,
                MembershipRole.SECRETARY,
                MembershipRole.CHAMA_ADMIN,
            },
            "Only auditor or higher can view chama transactions.",
        )

        data = list_all_transactions(
            filters=TransactionsFeedFilters(
                chama_id=chama_id,
                category=(query.validated_data.get("category") or None) or None,
                entry_type=(query.validated_data.get("entry_type") or None) or None,
                method=(query.validated_data.get("method") or None) or None,
                status=(query.validated_data.get("status") or None) or None,
                search=(query.validated_data.get("search") or None) or None,
                from_date=query.validated_data.get("from_date"),
                to_date=query.validated_data.get("to_date"),
                cursor=(query.validated_data.get("cursor") or None) or None,
                limit=int(query.validated_data.get("limit") or 50),
            )
        )
        return ApiResponse.success(data=data)


class TransactionDetailView(FinanceBaseView):
    def get(self, request, transaction_ref: str):
        chama_id = _resolve_chama_id(
            request,
            request.query_params.get("chama_id"),
            label="chama_id",
        )
        _require_roles(
            request.user,
            chama_id,
            {
                MembershipRole.AUDITOR,
                MembershipRole.TREASURER,
                MembershipRole.SECRETARY,
                MembershipRole.CHAMA_ADMIN,
            },
            "Only auditor or higher can view chama transactions.",
        )

        try:
            payload = get_transaction_detail(
                chama_id=chama_id,
                transaction_ref=transaction_ref,
            )
        except (PaymentIntent.DoesNotExist, JournalEntry.DoesNotExist, LedgerEntry.DoesNotExist, ValueError):
            return ApiResponse.error(
                message="We couldn’t load this transaction.",
                code="transaction_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        return ApiResponse.success(data=payload)


class LedgerReverseView(FinanceBaseView):
    def post(self, request, id):
        original = LedgerEntry.objects.filter(id=id).first()
        if not original:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        _require_roles(
            request.user,
            str(original.chama_id),
            {MembershipRole.CHAMA_ADMIN},
            "Only chama admin can reverse ledger entries.",
        )

        serializer = LedgerReverseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = FinanceService.reverse_ledger_entry(
                id, serializer.validated_data, request.user
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)

        return Response(
            {
                "reversal_entry": LedgerEntrySerializer(result.ledger_entry).data,
                "original_entry": LedgerEntrySerializer(result.created).data,
            },
            status=status.HTTP_201_CREATED,
        )


class DashboardView(FinanceBaseView):
    def get(self, request):
        query = DashboardQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)

        chama_id = str(query.validated_data["chama_id"])
        _require_roles(
            request.user,
            chama_id,
            {
                MembershipRole.AUDITOR,
                MembershipRole.TREASURER,
                MembershipRole.SECRETARY,
                MembershipRole.CHAMA_ADMIN,
            },
            "Only auditor or higher can view dashboard.",
        )

        data = FinanceService.compute_chama_dashboard(chama_id)
        return Response(data)


class LoanPortfolioView(FinanceBaseView):
    def get(self, request):
        query = LoanPortfolioQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        chama_id = str(query.validated_data["chama_id"])
        membership = _require_membership(request.user, chama_id)
        effective_role = get_effective_role(request.user, chama_id, membership)
        if effective_role == MembershipRole.MEMBER:
            return Response(
                {"detail": "Members cannot access portfolio summary."},
                status=status.HTTP_403_FORBIDDEN,
            )

        mask_members = bool(query.validated_data.get("mask_members", False))
        return Response(
            FinanceService.compute_loan_portfolio(chama_id, mask_members=mask_members)
        )


class LoanReportsView(FinanceBaseView):
    def get(self, request):
        query = LoanPortfolioQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        chama_id = str(query.validated_data["chama_id"])
        _require_roles(
            request.user,
            chama_id,
            {
                MembershipRole.AUDITOR,
                MembershipRole.TREASURER,
                MembershipRole.CHAMA_ADMIN,
            },
            "Only auditor, treasurer, or admin can view loan reports.",
        )
        return Response(
            {
                "applications": FinanceService.compute_loan_application_queue(
                    chama_id,
                    mask_members=False,
                ),
                "portfolio": FinanceService.compute_loan_portfolio(
                    chama_id,
                    mask_members=False,
                ),
            }
        )


class StatementView(FinanceBaseView):
    def get(self, request):
        query = StatementQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)

        chama_id = str(query.validated_data["chama_id"])
        membership = _require_membership(request.user, chama_id)

        member_id = str(query.validated_data.get("member_id") or request.user.id)
        effective_role = get_effective_role(request.user, chama_id, membership)
        if effective_role == MembershipRole.MEMBER and member_id != str(
            request.user.id
        ):
            raise PermissionDenied("Members can only view their own statements.")

        data = FinanceService.compute_member_statement(
            chama_id,
            member_id,
            query.validated_data.get("from"),
            query.validated_data.get("to"),
        )
        return Response(data)


class MonthlyAggregatesView(FinanceBaseView):
    def get(self, request):
        query = MonthlyAggregateQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)

        chama_id = str(query.validated_data["chama_id"])
        _require_roles(
            request.user,
            chama_id,
            {
                MembershipRole.AUDITOR,
                MembershipRole.TREASURER,
                MembershipRole.SECRETARY,
                MembershipRole.CHAMA_ADMIN,
            },
            "Only auditor or higher can view monthly aggregates.",
        )

        data = FinanceService.compute_monthly_aggregates(
            chama_id,
            months=query.validated_data["months"],
        )
        return Response(data)


class ManualAdjustmentView(FinanceBaseView):
    def post(self, request):
        serializer = ManualAdjustmentPostSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        chama_id = str(payload["chama_id"])
        _require_roles(
            request.user,
            chama_id,
            {MembershipRole.CHAMA_ADMIN},
            "Only chama admin can post manual adjustments.",
        )

        try:
            result = FinanceService.post_manual_adjustment(payload, request.user)
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)

        return Response(
            {
                "adjustment": ManualAdjustmentSerializer(result.created).data,
                "ledger_entry": LedgerEntrySerializer(result.ledger_entry).data,
            },
            status=status.HTTP_201_CREATED,
        )


class MonthCloseView(FinanceBaseView):
    def post(self, request):
        serializer = MonthCloseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        chama_id = str(payload["chama_id"])
        _require_roles(
            request.user,
            chama_id,
            {MembershipRole.CHAMA_ADMIN},
            "Only chama admin can close months.",
        )

        try:
            closure = FinanceService.close_month(payload, request.user)
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)

        return Response(
            MonthClosureSerializer(closure).data, status=status.HTTP_201_CREATED
        )


class MonthCloseListView(FinanceBaseView):
    def get(self, request):
        chama_id = _validate_uuid(request.query_params.get("chama_id"), "chama_id")
        _require_roles(
            request.user,
            chama_id,
            {
                MembershipRole.AUDITOR,
                MembershipRole.TREASURER,
                MembershipRole.SECRETARY,
                MembershipRole.CHAMA_ADMIN,
            },
            "Only auditor or higher can view month closures.",
        )

        queryset = MonthClosure.objects.filter(chama_id=chama_id).order_by("-month")
        return Response(MonthClosureSerializer(queryset, many=True).data)


class FinanceSummaryView(FinanceBaseView):
    def get(self, request):
        chama_id = _validate_uuid(request.query_params.get("chama_id"), "chama_id")
        _require_roles(
            request.user,
            chama_id,
            {
                MembershipRole.AUDITOR,
                MembershipRole.TREASURER,
                MembershipRole.CHAMA_ADMIN,
            },
            "Only auditor, treasurer, or admin can view finance summary.",
        )
        return Response(FinanceService.finance_summary(chama_id))


class FinanceReportsView(FinanceBaseView):
    def get(self, request):
        chama_id = _validate_uuid(request.query_params.get("chama_id"), "chama_id")
        _require_roles(
            request.user,
            chama_id,
            {
                MembershipRole.AUDITOR,
                MembershipRole.TREASURER,
                MembershipRole.CHAMA_ADMIN,
            },
            "Only auditor, treasurer, or admin can view finance reports.",
        )
        return Response(FinanceService.finance_reports(chama_id))


class MemberContributionSummaryView(FinanceBaseView):
    def get(self, request):
        chama_id = _validate_uuid(request.query_params.get("chama_id"), "chama_id")
        _require_roles(
            request.user,
            chama_id,
            {
                MembershipRole.AUDITOR,
                MembershipRole.TREASURER,
                MembershipRole.CHAMA_ADMIN,
            },
            "Only auditor, treasurer, or admin can view member contributions.",
        )
        return Response(FinanceService.member_contributions(chama_id))


class MemberContributionWorkspaceView(FinanceBaseView):
    billing_feature_key = "contributions_basic"
    skip_billing_access = True

    def get(self, request):
        chama_id = _resolve_chama_id(
            request,
            request.query_params.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        chama = get_object_or_404(Chama, id=chama_id)
        return Response(build_member_contribution_workspace(chama=chama, member=request.user))


class MemberLoanWorkspaceView(FinanceBaseView):
    billing_feature_key = "loans_basic"
    skip_billing_access = True

    def get(self, request):
        chama_id = _resolve_chama_id(
            request,
            request.query_params.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        chama = get_object_or_404(Chama, id=chama_id)
        return Response(build_member_loan_workspace(chama=chama, member=request.user))


class MemberWalletWorkspaceView(FinanceBaseView):
    billing_feature_key = "contributions_basic"
    skip_billing_access = True

    def get(self, request):
        chama_id = _resolve_chama_id(
            request,
            request.query_params.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        chama = get_object_or_404(Chama, id=chama_id)
        return ApiResponse.success(data=build_member_wallet_workspace(chama=chama, member=request.user))


class MemberWalletActivityView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def get(self, request):
        query = MemberWalletActivityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        chama_id = _resolve_chama_id(
            request,
            query.validated_data.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        chama = get_object_or_404(Chama, id=chama_id)
        return ApiResponse.success(
            data=build_member_wallet_activity(
                chama=chama,
                member=request.user,
                filter_key=query.validated_data.get("filter", "all"),
                search=query.validated_data.get("search"),
                start_date=query.validated_data.get("start_date"),
                end_date=query.validated_data.get("end_date"),
                limit=query.validated_data.get("limit", 50),
            )
        )


class MemberWalletTransactionDetailView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def get(self, request, transaction_ref):
        chama_id = _resolve_chama_id(
            request,
            request.query_params.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        chama = get_object_or_404(Chama, id=chama_id)

        try:
            return ApiResponse.success(
                data=build_member_wallet_transaction_detail(
                    chama=chama,
                    member=request.user,
                    transaction_ref=transaction_ref,
                )
            )
        except (PaymentIntent.DoesNotExist, LedgerEntry.DoesNotExist, ValueError):
            return ApiResponse.error(
                message="We couldn’t load this transaction.",
                code="wallet_transaction_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

    def post(self, request, transaction_ref):
        chama_id = _resolve_chama_id(
            request,
            request.data.get("chama_id") or request.query_params.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        chama = get_object_or_404(Chama, id=chama_id)

        if not transaction_ref.startswith("payment_"):
            return ApiResponse.error(
                message="We couldn’t refresh this payment status right now.",
                code="wallet_refresh_not_supported",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        intent_id = transaction_ref.replace("payment_", "", 1)
        try:
            intent = PaymentIntent.objects.filter(id=intent_id, chama=chama, user=request.user).first()
            if not intent:
                return ApiResponse.error(
                    message="We couldn’t load this transaction.",
                    code="wallet_transaction_not_found",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            if str(intent.status or "").lower() not in {
                "initiated",
                "pending",
                "pending_authentication",
                "pending_verification",
            }:
                return ApiResponse.success(
                    data=build_member_wallet_transaction_detail(
                        chama=chama,
                        member=request.user,
                        transaction_ref=transaction_ref,
                    )
                )

            try:
                UnifiedPaymentService.verify_payment(intent.id)
            except PaymentServiceError:
                return ApiResponse.error(
                    message="We couldn’t refresh this payment status right now.",
                    code="wallet_refresh_failed",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            return ApiResponse.success(
                data=build_member_wallet_transaction_detail(
                    chama=chama,
                    member=request.user,
                    transaction_ref=transaction_ref,
                )
            )
        except (PaymentIntent.DoesNotExist, LedgerEntry.DoesNotExist, ValueError):
            return ApiResponse.error(
                message="We couldn’t load this transaction.",
                code="wallet_transaction_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )


class MemberWalletDepositView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def post(self, request):
        serializer = MemberWalletDepositCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chama_id = _resolve_chama_id(
            request,
            serializer.validated_data.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        chama = get_object_or_404(Chama, id=chama_id)
        try:
            return ApiResponse.success(
                data=create_member_wallet_deposit(
                    chama=chama,
                    member=request.user,
                    amount=serializer.validated_data["amount"],
                    payment_method=serializer.validated_data["payment_method"],
                    phone=serializer.validated_data["phone"],
                    idempotency_key=serializer.validated_data.get("idempotency_key") or None,
                ),
                status_code=status.HTTP_201_CREATED,
            )
        except PaymentServiceError as exc:
            return ApiResponse.error(
                message=str(exc) or "We couldn’t start your deposit right now.",
                code="wallet_deposit_failed",
                status_code=status.HTTP_400_BAD_REQUEST,
            )


class MemberWalletDepositDetailView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def get(self, request, intent_id):
        serializer = MemberWalletDepositDetailSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        chama_id = _resolve_chama_id(
            request,
            serializer.validated_data.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        chama = get_object_or_404(Chama, id=chama_id)
        try:
            return ApiResponse.success(
                data=get_member_wallet_deposit_detail(
                    chama=chama,
                    member=request.user,
                    intent_id=str(intent_id),
                )
            )
        except PaymentIntent.DoesNotExist:
            return ApiResponse.error(
                message="We couldn’t load this transaction.",
                code="wallet_deposit_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

    def post(self, request, intent_id):
        serializer = MemberWalletDepositDetailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chama_id = _resolve_chama_id(
            request,
            serializer.validated_data.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        chama = get_object_or_404(Chama, id=chama_id)
        try:
            return ApiResponse.success(
                data=refresh_member_wallet_deposit(
                    chama=chama,
                    member=request.user,
                    intent_id=str(intent_id),
                )
            )
        except PaymentIntent.DoesNotExist:
            return ApiResponse.error(
                message="We couldn’t load this transaction.",
                code="wallet_deposit_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        except PaymentServiceError:
            return ApiResponse.error(
                message="We couldn’t refresh this payment status right now.",
                code="wallet_deposit_refresh_failed",
                status_code=status.HTTP_400_BAD_REQUEST,
            )


class MemberWalletWithdrawalView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def post(self, request):
        serializer = MemberWalletWithdrawalCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chama_id = _resolve_chama_id(
            request,
            serializer.validated_data.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        chama = get_object_or_404(Chama, id=chama_id)
        try:
            return ApiResponse.success(
                data=create_member_wallet_withdrawal(
                    chama=chama,
                    member=request.user,
                    amount=serializer.validated_data["amount"],
                    payment_method=serializer.validated_data["payment_method"],
                    phone=serializer.validated_data["phone"],
                    pin=str(serializer.validated_data.get("pin") or "").strip() or None,
                    idempotency_key=serializer.validated_data.get("idempotency_key") or None,
                ),
                status_code=status.HTTP_201_CREATED,
            )
        except PaymentServiceError as exc:
            message = str(exc) or "We couldn’t submit your withdrawal right now."
            return ApiResponse.error(
                message=message,
                code="wallet_withdrawal_failed",
                status_code=status.HTTP_400_BAD_REQUEST,
            )


class MemberWalletWithdrawalDetailView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def get(self, request, intent_id):
        serializer = MemberWalletWithdrawalDetailSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        chama_id = _resolve_chama_id(
            request,
            serializer.validated_data.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        chama = get_object_or_404(Chama, id=chama_id)
        try:
            return ApiResponse.success(
                data=get_member_wallet_withdrawal_detail(
                    chama=chama,
                    member=request.user,
                    intent_id=str(intent_id),
                )
            )
        except PaymentIntent.DoesNotExist:
            return ApiResponse.error(
                message="We couldn’t load this transaction.",
                code="wallet_withdrawal_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

    def post(self, request, intent_id):
        serializer = MemberWalletWithdrawalDetailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chama_id = _resolve_chama_id(
            request,
            serializer.validated_data.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        chama = get_object_or_404(Chama, id=chama_id)
        try:
            return ApiResponse.success(
                data=refresh_member_wallet_withdrawal(
                    chama=chama,
                    member=request.user,
                    intent_id=str(intent_id),
                )
            )
        except PaymentIntent.DoesNotExist:
            return ApiResponse.error(
                message="We couldn’t load this transaction.",
                code="wallet_withdrawal_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )


class MemberWalletTransferView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def post(self, request):
        serializer = MemberWalletTransferCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chama_id = _resolve_chama_id(
            request,
            serializer.validated_data.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        chama = get_object_or_404(Chama, id=chama_id)
        try:
            return ApiResponse.success(
                data=create_member_wallet_transfer(
                    chama=chama,
                    member=request.user,
                    recipient_member_id=str(serializer.validated_data["recipient_member_id"]),
                    amount=serializer.validated_data["amount"],
                    idempotency_key=serializer.validated_data.get("idempotency_key") or None,
                    note=serializer.validated_data.get("note", ""),
                ),
                status_code=status.HTTP_201_CREATED,
            )
        except PaymentServiceError as exc:
            return ApiResponse.error(
                message=str(exc) or "We couldn’t complete this transfer right now.",
                code="wallet_transfer_failed",
                status_code=status.HTTP_400_BAD_REQUEST,
            )


class MemberWalletContributionView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def post(self, request):
        serializer = MemberWalletContributionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chama_id = _resolve_chama_id(
            request,
            serializer.validated_data.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        chama = get_object_or_404(Chama, id=chama_id)
        try:
            return ApiResponse.success(
                data=create_member_wallet_contribution(
                    chama=chama,
                    member=request.user,
                    contribution_type_id=str(serializer.validated_data["contribution_type_id"]),
                    amount=serializer.validated_data["amount"],
                    idempotency_key=serializer.validated_data.get("idempotency_key") or None,
                ),
                status_code=status.HTTP_201_CREATED,
            )
        except PaymentServiceError as exc:
            return ApiResponse.error(
                message=str(exc) or "We couldn’t submit this contribution right now.",
                code="wallet_contribution_failed",
                status_code=status.HTTP_400_BAD_REQUEST,
            )


class MemberPenaltyListView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def get(self, request):
        chama_id = _resolve_chama_id(
            request,
            request.query_params.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        chama = get_object_or_404(Chama, id=chama_id)
        return Response(build_member_penalties(chama=chama, member=request.user))


class ContributionDetailView(FinanceBaseView):
    billing_feature_key = "contributions_basic"

    def get(self, request, id):
        contribution = (
            Contribution.objects.select_related("member", "recorded_by", "contribution_type")
            .filter(id=id)
            .first()
        )
        if not contribution:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        membership = _require_membership(request.user, str(contribution.chama_id))
        effective_role = get_effective_role(request.user, str(contribution.chama_id), membership)
        if effective_role == MembershipRole.MEMBER and contribution.member_id != request.user.id:
            raise PermissionDenied("Members can only view their own contributions.")
        if effective_role == MembershipRole.AUDITOR and contribution.member_id != request.user.id:
            raise PermissionDenied("Auditor cannot view member contributions directly.")

        return Response(build_member_contribution_detail(contribution=contribution))


class ExpenseListCreateView(FinanceBaseView):
    def get(self, request):
        chama_id = _validate_uuid(request.query_params.get("chama_id"), "chama_id")
        _require_roles(
            request.user,
            chama_id,
            {
                MembershipRole.AUDITOR,
                MembershipRole.TREASURER,
                MembershipRole.CHAMA_ADMIN,
            },
            "Only auditor, treasurer, or admin can view expenses.",
        )
        queryset = Expense.objects.filter(chama_id=chama_id).order_by("-expense_date", "-created_at")
        return Response(
            ExpenseSerializer(queryset, many=True, context={"request": request}).data
        )

    def post(self, request):
        serializer = ExpenseCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        chama_id = str(payload["chama_id"])
        _require_roles(
            request.user,
            chama_id,
            {
                MembershipRole.MEMBER,
                MembershipRole.TREASURER,
                MembershipRole.CHAMA_ADMIN,
            },
            "Only active members can submit expenses.",
        )
        try:
            expense = FinanceService.submit_expense(payload, request.user)
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(
            {
                "expense": ExpenseSerializer(
                    expense, context={"request": request}
                ).data,
            },
            status=status.HTTP_201_CREATED,
        )


class ExpenseCategoryListCreateView(FinanceBaseView):
    def get(self, request):
        chama_id = _resolve_chama_id(
            request,
            request.query_params.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        queryset = ExpenseCategory.objects.filter(chama_id=chama_id).order_by("name")
        return Response(ExpenseCategorySerializer(queryset, many=True).data)

    def post(self, request):
        serializer = ExpenseCategorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chama_value = serializer.validated_data.get("chama")
        chama_id = _validate_uuid(getattr(chama_value, "id", chama_value), "chama_id")
        _require_roles(
            request.user,
            chama_id,
            {MembershipRole.TREASURER, MembershipRole.CHAMA_ADMIN},
            "Only treasurer or admin can manage expense categories.",
        )
        category = serializer.save(created_by=request.user, updated_by=request.user)
        return Response(ExpenseCategorySerializer(category).data, status=status.HTTP_201_CREATED)


class ExpenseApproveView(FinanceBaseView):
    def post(self, request, id):
        expense = get_object_or_404(Expense, id=id)
        _require_roles(
            request.user,
            str(expense.chama_id),
            {MembershipRole.TREASURER, MembershipRole.CHAMA_ADMIN},
            "Only treasurer or admin can approve expenses.",
        )
        serializer = ExpenseDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            updated = FinanceService.approve_expense(
                expense_id=id,
                payload=serializer.validated_data,
                actor=request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(ExpenseSerializer(updated, context={"request": request}).data)


class ExpenseRejectView(FinanceBaseView):
    def post(self, request, id):
        expense = get_object_or_404(Expense, id=id)
        _require_roles(
            request.user,
            str(expense.chama_id),
            {MembershipRole.TREASURER, MembershipRole.CHAMA_ADMIN},
            "Only treasurer or admin can reject expenses.",
        )
        serializer = ExpenseDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            updated = FinanceService.reject_expense(
                expense_id=id,
                payload=serializer.validated_data,
                actor=request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(ExpenseSerializer(updated, context={"request": request}).data)


class ExpenseMarkPaidView(FinanceBaseView):
    def post(self, request, id):
        expense = get_object_or_404(Expense, id=id)
        _require_roles(
            request.user,
            str(expense.chama_id),
            {MembershipRole.TREASURER, MembershipRole.CHAMA_ADMIN},
            "Only treasurer or admin can mark expenses paid.",
        )
        serializer = ExpenseMarkPaidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = FinanceService.mark_expense_paid(
                expense_id=id,
                payload=serializer.validated_data,
                actor=request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)
        return Response(
            {
                "expense": ExpenseSerializer(
                    result.created, context={"request": request}
                ).data,
                "ledger_entry": LedgerEntrySerializer(result.ledger_entry).data,
            }
        )


class FinancialSnapshotListView(FinanceBaseView):
    def get(self, request):
        chama_id = _validate_uuid(request.query_params.get("chama_id"), "chama_id")
        _require_roles(
            request.user,
            chama_id,
            {
                MembershipRole.AUDITOR,
                MembershipRole.TREASURER,
                MembershipRole.CHAMA_ADMIN,
            },
            "Only auditor, treasurer, or admin can view financial snapshots.",
        )
        queryset = FinancialSnapshot.objects.filter(chama_id=chama_id).order_by("-snapshot_date")[:90]
        return Response(FinancialSnapshotSerializer(queryset, many=True).data)


class FastMobileDashboardSummary(FinanceBaseView):
    """Cached dashboard summary for mobile - combines wallet, contributions, loans in one fast call."""

    skip_billing_access = True

    def get(self, request):
        from apps.notifications.models import Notification, NotificationInboxStatus

        chama_id = _resolve_chama_id(
            request,
            request.query_params.get("chama_id"),
            label="chama_id",
        )
        _require_membership(request.user, chama_id)
        chama = get_object_or_404(Chama, id=chama_id)
        member = request.user

        cache_key = f"mobile_dashboard:{chama_id}:{member.id}"
        cached = cache.get(cache_key)
        if cached:
            cached["_cached"] = True
            return Response(cached)

        wallet = build_member_wallet_workspace(chama=chama, member=member)
        contributions = build_member_contribution_workspace(chama=chama, member=member)
        loans = build_member_loan_workspace(chama=chama, member=member)

        unread_count = Notification.objects.filter(
            target_user=member,
            inbox_status=NotificationInboxStatus.UNREAD,
            chama_id=chama_id,
        ).count()

        response_data = {
            "wallet": wallet.get("summary", {}),
            "contributions": contributions.get("summary", {}),
            "loans": loans.get("summary", {}),
            "unread_notifications": unread_count,
            "_cached": False,
        }

        cache.set(cache_key, response_data, 30)

        return Response(response_data)
