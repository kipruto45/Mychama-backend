import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from django.http import Http404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.gating import BillingAccessMixin
from apps.chama.models import MembershipRole
from apps.chama.services import get_effective_role, is_member_suspended
from apps.finance.models import (
    Contribution,
    ContributionGoal,
    ContributionGoalStatus,
    ContributionType,
    InstallmentSchedule,
    LedgerEntry,
    Loan,
    LoanApprovalLog,
    LoanGuarantor,
    LoanRestructureRequest,
    LoanTopUpRequest,
    LoanProduct,
    MonthClosure,
    Penalty,
)
from apps.finance.permissions import get_chama_membership
from apps.finance.serializers import (
    ContributionGoalSerializer,
    ContributionGoalUpsertSerializer,
    ContributionRecordSerializer,
    ContributionSerializer,
    ContributionTypeSerializer,
    CreditScoreQuerySerializer,
    DashboardQuerySerializer,
    IdempotencyOnlySerializer,
    InstallmentScheduleSerializer,
    LedgerEntrySerializer,
    LedgerQuerySerializer,
    LedgerReverseSerializer,
    LoanApprovalLogSerializer,
    LoanGuarantorCreateSerializer,
    LoanGuarantorSerializer,
    LoanEligibilitySerializer,
    LoanPortfolioQuerySerializer,
    LoanProductSerializer,
    LoanRestructureRequestCreateSerializer,
    LoanRestructureRequestSerializer,
    LoanRestructureReviewSerializer,
    LoanRequestSerializer,
    LoanReviewSerializer,
    LoanSerializer,
    LoanTopUpRequestCreateSerializer,
    LoanTopUpRequestSerializer,
    LoanTopUpReviewSerializer,
    ManualAdjustmentPostSerializer,
    ManualAdjustmentSerializer,
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
from apps.finance.services import (
    FinanceService,
    FinanceServiceError,
    IdempotencyConflictError,
    MonthClosedError,
)


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

    def _handle_service_error(self, exc: Exception):
        if isinstance(exc, IdempotencyConflictError):
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        if isinstance(exc, MonthClosedError):
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        if isinstance(exc, FinanceServiceError):
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if isinstance(exc, Http404):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
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
    def post(self, request):
        serializer = LoanEligibilitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

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


class LoanRequestView(FinanceBaseView):
    def post(self, request):
        serializer = LoanRequestSerializer(data=request.data)
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

        try:
            loan = FinanceService.request_loan(payload, request.user)
        except Exception as exc:  # noqa: BLE001
            return self._handle_service_error(exc)

        return Response(LoanSerializer(loan).data, status=status.HTTP_201_CREATED)


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
        effective_role = get_effective_role(request.user, chama_id, membership)
        if effective_role == MembershipRole.MEMBER:
            queryset = queryset.filter(member=request.user)

        return Response(LoanSerializer(queryset, many=True).data)


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
