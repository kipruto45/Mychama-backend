import uuid

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chama.models import Chama, Membership, MembershipRole
from apps.chama.services import get_effective_role
from apps.finance.models import Loan
from apps.issues.models import (
    Issue,
    IssueActivityAction,
    IssueAppeal,
    IssueAutoTriggerLog,
    IssueCategory,
    IssueComment,
    IssueCommentVisibility,
    IssueEvidence,
    IssueMediationNote,
    IssuePriority,
    IssueRating,
    IssueResolution,
    IssueResolutionStatus,
    IssueStatus,
)
from apps.issues.permissions import (
    can_add_internal_comment,
    can_approve_resolution,
    can_assign_issue,
    can_comment_issue,
    can_create_system_issue,
    can_edit_issue,
    can_escalate_issue,
    can_execute_resolution,
    can_issue_warning,
    can_moderate_issue,
    can_rate_issue,
    can_reopen_issue,
    can_suspend_user,
    can_view_issue,
    can_view_stats,
    filter_issue_queryset,
    get_issue_membership,
)
from apps.issues.serializers import (
    INTERNAL_NOTE_ROLES,
    IssueActivityLogSerializer,
    IssueAppealCreateSerializer,
    IssueAppealReviewSerializer,
    IssueAppealSerializer,
    IssueAssignSerializer,
    IssueAutoTriggerLogSerializer,
    IssueChairpersonDecisionSerializer,
    IssueClarificationRequestSerializer,
    IssueClarificationResponseSerializer,
    IssueCommentCreateSerializer,
    IssueCommentSerializer,
    IssueCreateSerializer,
    IssueDetailSerializer,
    IssueDismissSerializer,
    IssueEscalateSerializer,
    IssueEvidenceCreateSerializer,
    IssueEvidenceSerializer,
    IssueExportSerializer,
    IssueFilterSerializer,
    IssueInvestigationUpdateSerializer,
    IssueLiftSuspensionSerializer,
    IssueListSerializer,
    IssueMediationNoteCreateSerializer,
    IssueMediationNoteSerializer,
    IssueRatingSerializer,
    IssueReopenSerializer,
    IssueResolutionApproveSerializer,
    IssueResolutionProposeSerializer,
    IssueResolutionRejectSerializer,
    IssueResolutionSerializer,
    IssueStatsQuerySerializer,
    IssueStatusUpdateSerializer,
    IssueSuspendSerializer,
    IssueUpdateSerializer,
    IssueWarnSerializer,
    SuspensionSerializer,
    WarningSerializer,
)
from apps.issues.services import (
    IssueServiceError,
    add_comment,
    add_evidence,
    approve_resolution,
    assign_issue,
    build_issue_stats,
    chairperson_approve_resolution,
    chairperson_reject_resolution,
    change_issue_status,
    create_issue,
    create_issue_appeal,
    create_mediation_note,
    create_system_issue,
    dismiss_issue,
    escalate_issue,
    escalate_issue_ladder,
    get_allowed_actions,
    issue_warning,
    lift_user_suspension,
    log_issue_activity,
    propose_resolution,
    rate_issue,
    reject_resolution,
    request_clarification,
    respond_to_clarification,
    review_issue_appeal,
    start_investigation,
    suspend_reported_user,
    update_investigation,
)
from apps.notifications.models import NotificationPriority, NotificationType
from apps.notifications.services import NotificationService
from core.concurrency import enforce_if_unmodified_since
from core.pagination import DefaultPagination
from core.throttles import IssueCreateRateThrottle, IssueModerationRateThrottle


class IssueScopeMixin:
    def _as_uuid(self, raw_value, source_label: str) -> str | None:
        if raw_value in [None, ""]:
            return None
        try:
            return str(uuid.UUID(str(raw_value)))
        except (ValueError, TypeError) as exc:
            raise ValidationError(
                {"detail": f"Invalid chama id in {source_label}."}
            ) from exc

    def resolve_chama_scope(self, explicit_chama_id=None) -> str:
        from_payload = self._as_uuid(explicit_chama_id, "request payload/query")
        from_header = self._as_uuid(
            self.request.headers.get("X-CHAMA-ID"),
            "X-CHAMA-ID header",
        )

        candidates = [item for item in [from_payload, from_header] if item]
        if not candidates:
            raise ValidationError(
                {
                    "chama_id": (
                        "Provide chama_id in request body/query "
                        "or X-CHAMA-ID header."
                    )
                }
            )

        if len(set(candidates)) > 1:
            raise ValidationError(
                {"detail": "Chama scope values do not match across request sources."}
            )

        return candidates[0]

    def require_membership(self, chama_id):
        if self.request.user.is_superuser:
            return None

        membership = get_issue_membership(self.request.user, chama_id)
        if not membership:
            raise PermissionDenied(
                "You are not an approved active member of this chama."
            )
        return membership

    def _effective_role(self, membership, chama_id: str | None = None) -> str | None:
        if not membership:
            return None
        scoped_chama_id = chama_id or str(membership.chama_id)
        return get_effective_role(self.request.user, scoped_chama_id, membership)

    def validate_reported_user_membership(self, chama_id, reported_user_id):
        if not reported_user_id:
            return
        exists = Membership.objects.filter(
            chama_id=chama_id,
            user_id=reported_user_id,
            is_approved=True,
        ).exists()
        if not exists:
            raise ValidationError(
                {
                    "reported_user_id": (
                        "Reported user must be an approved member in this chama."
                    )
                }
            )

    def validate_loan_context(self, chama_id, loan_id):
        if not loan_id:
            return
        exists = Loan.objects.filter(id=loan_id, chama_id=chama_id).exists()
        if not exists:
            raise ValidationError(
                {"loan_id": "Loan must belong to the same chama scope."}
            )

    def get_issue_and_membership(self, issue_id):
        issue = get_object_or_404(
            Issue.objects.select_related(
                "chama",
                "created_by",
                "assigned_to",
                "reported_user",
                "loan",
            ),
            id=issue_id,
        )
        membership = self.require_membership(str(issue.chama_id))
        if not can_view_issue(self.request.user, membership, issue):
            raise PermissionDenied("You are not allowed to view this issue.")
        return issue, membership

    def serialize_issue(self, issue, membership):
        return IssueDetailSerializer(
            issue,
            context={"request": self.request, "membership": membership},
        )

    def _apply_role_scope(self, queryset, membership):
        return filter_issue_queryset(queryset, self.request.user, membership)


class IssueListCreateView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method == "POST":
            return [IssueCreateRateThrottle()]
        return super().get_throttles()

    def get(self, request):
        filters_serializer = IssueFilterSerializer(data=request.query_params)
        filters_serializer.is_valid(raise_exception=True)

        scoped_chama_id = self.resolve_chama_scope(
            filters_serializer.validated_data.get("chama_id")
        )
        membership = self.require_membership(scoped_chama_id)

        queryset = (
            Issue.objects.select_related(
                "created_by",
                "assigned_to",
                "reported_user",
                "loan",
            )
            .prefetch_related("comments", "evidences", "resolutions", "ratings")
            .filter(chama_id=scoped_chama_id)
        )
        queryset = self._apply_role_scope(queryset, membership)

        filters = filters_serializer.validated_data
        if filters.get("status"):
            queryset = queryset.filter(status=filters["status"])
        if filters.get("category"):
            queryset = queryset.filter(category=filters["category"])
        if filters.get("severity"):
            queryset = queryset.filter(severity=filters["severity"])
        if filters.get("source_type"):
            queryset = queryset.filter(source_type=filters["source_type"])
        if filters.get("issue_scope"):
            queryset = queryset.filter(issue_scope=filters["issue_scope"])
        if filters.get("assigned_to"):
            queryset = queryset.filter(assigned_to_id=filters["assigned_to"])
        if filters.get("reported_user"):
            queryset = queryset.filter(reported_user_id=filters["reported_user"])
        if filters.get("loan_id"):
            queryset = queryset.filter(loan_id=filters["loan_id"])
        if filters.get("created_by"):
            queryset = queryset.filter(created_by_id=filters["created_by"])
        if filters.get("escalation_type"):
            queryset = queryset.filter(escalation_type=filters["escalation_type"])
        if filters.get("reopened"):
            queryset = queryset.filter(reopened_count__gt=0)
        if filters.get("date_from"):
            queryset = queryset.filter(created_at__date__gte=filters["date_from"])
        if filters.get("date_to"):
            queryset = queryset.filter(created_at__date__lte=filters["date_to"])

        search = filters.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search)
                | Q(description__icontains=search)
                | Q(issue_code__icontains=search)
                | Q(created_by__full_name__icontains=search)
                | Q(created_by__phone__icontains=search)
                | Q(reported_user__full_name__icontains=search)
                | Q(reported_user__phone__icontains=search)
            )

        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset.order_by("-created_at"), request)
        serializer = IssueListSerializer(
            page,
            many=True,
            context={"request": request, "membership": membership},
        )
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        serializer = IssueCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        scoped_chama_id = self.resolve_chama_scope(
            serializer.validated_data.get("chama_id")
        )
        membership = self.require_membership(scoped_chama_id)
        role = self._effective_role(membership, scoped_chama_id)

        if membership and role == MembershipRole.AUDITOR:
            raise PermissionDenied("Auditor role has read-only access.")

        self.validate_reported_user_membership(
            scoped_chama_id,
            serializer.validated_data.get("reported_user_id"),
        )
        self.validate_loan_context(
            scoped_chama_id,
            serializer.validated_data.get("loan_id"),
        )

        chama = get_object_or_404(Chama, id=scoped_chama_id)

        source_type = serializer.validated_data.get("source_type", "member")
        if membership:
            if role == MembershipRole.CHAMA_ADMIN:
                source_type = "chairperson"
            elif role == MembershipRole.TREASURER:
                source_type = "treasurer"

        issue = create_issue(
            chama=chama,
            title=serializer.validated_data["title"],
            description=serializer.validated_data["description"],
            category=serializer.validated_data.get("category", IssueCategory.OPERATIONAL),
            severity=serializer.validated_data.get("severity", IssuePriority.MEDIUM),
            raised_by=request.user,
            source_type=source_type,
            issue_scope=serializer.validated_data.get("issue_scope", "personal"),
            reported_user_id=serializer.validated_data.get("reported_user_id"),
            loan_id=serializer.validated_data.get("loan_id"),
            report_type=serializer.validated_data.get("report_type", ""),
            is_anonymous=serializer.validated_data.get("is_anonymous", False),
            due_at=serializer.validated_data.get("due_at"),
        )

        return Response(
            self.serialize_issue(issue, membership).data,
            status=status.HTTP_201_CREATED,
        )


class IssueDetailView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        data = self.serialize_issue(issue, membership).data
        allowed_actions = get_allowed_actions(issue, request.user, membership)
        data["allowed_actions"] = allowed_actions
        return Response(data)

    def patch(self, request, id):
        issue = get_object_or_404(Issue, id=id)
        membership = self.require_membership(str(issue.chama_id))
        
        if not can_edit_issue(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to edit this issue.")
            
        if not request.user.is_superuser and issue.status not in {IssueStatus.OPEN, IssueStatus.REOPENED}:
            raise ValidationError(
                {"detail": "Issue can only be edited while status is OPEN or REOPENED."}
            )
        enforce_if_unmodified_since(request, current_updated_at=issue.updated_at)

        serializer = IssueUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        self.validate_reported_user_membership(
            str(issue.chama_id),
            serializer.validated_data.get("reported_user_id"),
        )
        self.validate_loan_context(
            str(issue.chama_id),
            serializer.validated_data.get("loan_id"),
        )

        changed_fields = []
        for field in [
            "title",
            "description",
            "category",
            "severity",
            "report_type",
            "is_anonymous",
            "due_at",
        ]:
            if field in serializer.validated_data:
                setattr(issue, field, serializer.validated_data[field])
                changed_fields.append(field)

        if "reported_user_id" in serializer.validated_data:
            issue.reported_user_id = serializer.validated_data.get("reported_user_id")
            changed_fields.append("reported_user")
        if "loan_id" in serializer.validated_data:
            issue.loan_id = serializer.validated_data.get("loan_id")
            changed_fields.append("loan")

        issue.updated_by = request.user
        issue.save()

        log_issue_activity(
            issue,
            request.user,
            IssueActivityAction.UPDATED,
            {"updated_fields": changed_fields},
        )

        return Response(self.serialize_issue(issue, membership).data)


class IssueAssignView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_assign_issue(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to assign this issue.")

        serializer = IssueAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        assignee_membership = (
            Membership.objects.select_related("user")
            .filter(
                chama=issue.chama,
                user_id=serializer.validated_data["assigned_to_id"],
                is_active=True,
                is_approved=True,
            )
            .first()
        )
        if not assignee_membership:
            raise ValidationError(
                {"assigned_to_id": "Assigned user must be an active chama member."}
            )

        updated_issue = assign_issue(
            issue,
            assignee_membership.user,
            request.user,
            assigned_role=serializer.validated_data.get("assigned_role", ""),
            note=serializer.validated_data.get("note", ""),
        )

        return Response(self.serialize_issue(updated_issue, membership).data)


class IssueClarificationRequestView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_moderate_issue(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to request clarification.")

        serializer = IssueClarificationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            comment = request_clarification(
                issue,
                request.user,
                serializer.validated_data["message"],
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(
            IssueCommentSerializer(comment).data,
            status=status.HTTP_201_CREATED,
        )


class IssueClarificationResponseView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if issue.created_by_id != request.user.id:
            raise PermissionDenied("Only the issue creator can respond to clarification.")

        serializer = IssueClarificationResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            comment = respond_to_clarification(
                issue,
                request.user,
                serializer.validated_data["message"],
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(
            IssueCommentSerializer(comment).data,
            status=status.HTTP_201_CREATED,
        )


class IssueStartInvestigationView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_moderate_issue(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to start investigation.")

        note = request.data.get("note", "") if hasattr(request.data, "get") else ""

        try:
            updated_issue = start_investigation(issue, request.user, note)
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(self.serialize_issue(updated_issue, membership).data)


class IssueInvestigationUpdateView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_moderate_issue(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to add investigation updates.")

        serializer = IssueInvestigationUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            comment = update_investigation(
                issue,
                request.user,
                serializer.validated_data["note"],
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(
            IssueCommentSerializer(comment).data,
            status=status.HTTP_201_CREATED,
        )


class IssueProposeResolutionView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_moderate_issue(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to propose resolution.")

        serializer = IssueResolutionProposeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            resolution = propose_resolution(
                issue,
                request.user,
                resolution_type=serializer.validated_data["resolution_type"],
                summary=serializer.validated_data["summary"],
                detailed_action_taken=serializer.validated_data.get("detailed_action_taken", ""),
                financial_adjustment_amount=serializer.validated_data.get("financial_adjustment_amount"),
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(
            IssueResolutionSerializer(resolution).data,
            status=status.HTTP_201_CREATED,
        )


class IssueApproveResolutionView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_approve_resolution(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to approve resolution.")

        resolution = issue.resolutions.filter(
            status=IssueResolutionStatus.PROPOSED
        ).first()
        if not resolution:
            raise ValidationError({"detail": "No proposed resolution found."})

        try:
            resolution = approve_resolution(resolution, request.user, issue)
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(IssueResolutionSerializer(resolution).data)


class IssueRejectResolutionView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_approve_resolution(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to reject resolution.")

        serializer = IssueResolutionRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        resolution = issue.resolutions.filter(
            status=IssueResolutionStatus.PROPOSED
        ).first()
        if not resolution:
            raise ValidationError({"detail": "No proposed resolution found."})

        try:
            resolution = reject_resolution(
                resolution,
                request.user,
                serializer.validated_data["reason"],
                issue,
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(IssueResolutionSerializer(resolution).data)


class IssueChairpersonApproveView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_execute_resolution(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to approve as chairperson.")

        try:
            updated_issue = chairperson_approve_resolution(issue, request.user)
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(self.serialize_issue(updated_issue, membership).data)


class IssueChairpersonRejectView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_execute_resolution(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to reject as chairperson.")

        serializer = IssueChairpersonDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            updated_issue = chairperson_reject_resolution(
                issue,
                request.user,
                serializer.validated_data.get("reason", ""),
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(self.serialize_issue(updated_issue, membership).data)


class IssueDismissView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_moderate_issue(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to dismiss this issue.")

        serializer = IssueDismissSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            updated_issue = dismiss_issue(
                issue,
                request.user,
                serializer.validated_data["reason"],
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(self.serialize_issue(updated_issue, membership).data)


class IssueEscalateView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_escalate_issue(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to escalate this issue.")

        serializer = IssueEscalateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            updated_issue = escalate_issue(
                issue,
                request.user,
                escalation_type=serializer.validated_data["escalation_type"],
                reason=serializer.validated_data.get("reason", ""),
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(self.serialize_issue(updated_issue, membership).data)


class IssueReopenView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_reopen_issue(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to reopen this issue.")

        serializer = IssueReopenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            updated_issue = reopen_issue(
                issue,
                request.user,
                serializer.validated_data["reason"],
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(self.serialize_issue(updated_issue, membership).data)


class IssueRateView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_rate_issue(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to rate this issue.")

        serializer = IssueRatingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            rating = rate_issue(
                issue,
                request.user,
                serializer.validated_data["score"],
                serializer.validated_data.get("feedback", ""),
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(
            IssueRatingSerializer(rating).data,
            status=status.HTTP_201_CREATED,
        )


class IssueCommentCreateView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_comment_issue(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to comment on this issue.")

        serializer = IssueCommentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        is_internal = serializer.validated_data.get("visibility") == IssueCommentVisibility.INTERNAL_ONLY
        if (
            is_internal
            and not request.user.is_superuser
            and not can_add_internal_comment(request.user, membership, issue)
        ):
            raise PermissionDenied(
                "Only admin, secretary, or treasurer can add internal comments."
            )

        try:
            comment = add_comment(
                issue,
                request.user,
                serializer.validated_data["body"],
                comment_type=serializer.validated_data.get("comment_type", "public_update"),
                visibility=serializer.validated_data.get("visibility", "member_visible"),
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(
            IssueCommentSerializer(comment).data,
            status=status.HTTP_201_CREATED,
        )


class IssueEvidenceCreateView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_comment_issue(request.user, membership, issue):
            raise PermissionDenied(
                "You are not allowed to upload evidence on this issue."
            )

        serializer = IssueEvidenceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            evidence = add_evidence(
                issue,
                request.user,
                serializer.validated_data["file"],
                evidence_type=serializer.validated_data.get("evidence_type", "other"),
                caption=serializer.validated_data.get("caption", ""),
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(
            IssueEvidenceSerializer(evidence).data,
            status=status.HTTP_201_CREATED,
        )


class IssueStatusUpdateView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_moderate_issue(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to update issue status.")

        serializer = IssueStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            updated_issue = change_issue_status(
                issue,
                serializer.validated_data["status"],
                request.user,
                note=serializer.validated_data.get("note", ""),
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(self.serialize_issue(updated_issue, membership).data)


class IssueCloseView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_moderate_issue(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to close this issue.")

        note = request.data.get("note", "") if hasattr(request.data, "get") else ""
        try:
            updated_issue = change_issue_status(
                issue,
                IssueStatus.CLOSED,
                request.user,
                note=note,
                force=True,
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(self.serialize_issue(updated_issue, membership).data)


class IssueWarnView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        role = self._effective_role(membership, str(issue.chama_id))
        if not can_issue_warning(request.user, membership, issue):
            raise PermissionDenied("Only admin/secretary can issue warnings.")

        serializer = IssueWarnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            warning = issue_warning(
                issue,
                actor=request.user,
                reason=serializer.validated_data["reason"],
                severity=serializer.validated_data["severity"],
                message_to_user=serializer.validated_data.get("message_to_user", ""),
                channels=serializer.validated_data.get("channels", ["sms", "email"]),
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(WarningSerializer(warning).data, status=status.HTTP_201_CREATED)


class IssueSuspendView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_suspend_user(request.user, membership, issue):
            raise PermissionDenied("Only admin can suspend users.")

        serializer = IssueSuspendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            suspension = suspend_reported_user(
                issue,
                actor=request.user,
                reason=serializer.validated_data["reason"],
                starts_at=serializer.validated_data.get("starts_at"),
                ends_at=serializer.validated_data.get("ends_at"),
                message_to_user=serializer.validated_data.get("message_to_user", ""),
                channels=serializer.validated_data.get("channels", ["sms", "email"]),
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(
            SuspensionSerializer(suspension).data,
            status=status.HTTP_201_CREATED,
        )


class IssueLiftSuspensionView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_suspend_user(request.user, membership, issue):
            raise PermissionDenied("Only admin can lift suspensions.")

        serializer = IssueLiftSuspensionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            suspension = lift_user_suspension(
                issue,
                actor=request.user,
                lift_reason=serializer.validated_data.get("lift_reason", ""),
                channels=serializer.validated_data.get("channels", ["sms", "email"]),
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(SuspensionSerializer(suspension).data)


class IssueMediationNoteListCreateView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        role = self._effective_role(membership, str(issue.chama_id))
        if not request.user.is_superuser and (
            not membership
            or role
            not in {
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.SECRETARY,
                MembershipRole.TREASURER,
                MembershipRole.AUDITOR,
            }
        ):
            raise PermissionDenied("Only leaders/auditor can view mediation notes.")
        queryset = IssueMediationNote.objects.filter(issue=issue).order_by("created_at")
        return Response(IssueMediationNoteSerializer(queryset, many=True).data)

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        role = self._effective_role(membership, str(issue.chama_id))
        if not request.user.is_superuser and (
            not membership
            or role
            not in {
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.SECRETARY,
                MembershipRole.TREASURER,
            }
        ):
            raise PermissionDenied(
                "Only admin/secretary/treasurer can add mediation notes."
            )

        serializer = IssueMediationNoteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = create_mediation_note(
            issue,
            actor=request.user,
            note=serializer.validated_data["note"],
            is_private=serializer.validated_data.get("is_private", True),
        )
        return Response(
            IssueMediationNoteSerializer(note).data, status=status.HTTP_201_CREATED
        )


class IssueEscalationLadderView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_escalate_issue(request.user, membership, issue):
            raise PermissionDenied("Only admin can escalate issues.")

        serializer = IssueEscalateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = escalate_issue_ladder(
                issue,
                actor=request.user,
                reason=serializer.validated_data.get("reason", ""),
                channels=serializer.validated_data.get("channels", ["sms", "email"]),
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            {
                "issue_id": str(issue.id),
                "result": result,
            },
            status=status.HTTP_200_OK,
        )


class IssueAppealCreateView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        role = self._effective_role(membership, str(issue.chama_id))
        if membership and role == MembershipRole.AUDITOR:
            raise PermissionDenied("Auditor role has read-only access.")
        if not request.user.is_superuser and issue.created_by_id != request.user.id:
            raise PermissionDenied("Only issue creator can file an appeal.")

        serializer = IssueAppealCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            appeal = create_issue_appeal(
                issue,
                actor=request.user,
                message=serializer.validated_data["message"],
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            IssueAppealSerializer(appeal).data, status=status.HTTP_201_CREATED
        )


class IssueAppealReviewView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, appeal_id):
        appeal = get_object_or_404(
            IssueAppeal.objects.select_related("issue", "issue__chama"),
            id=appeal_id,
        )
        membership = self.require_membership(str(appeal.issue.chama_id))
        role = self._effective_role(membership, str(appeal.issue.chama_id))
        if not request.user.is_superuser and (
            not membership
            or role
            not in {
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.SECRETARY,
            }
        ):
            raise PermissionDenied("Only admin/secretary can review appeals.")

        serializer = IssueAppealReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            reviewed = review_issue_appeal(
                appeal,
                actor=request.user,
                status=serializer.validated_data["status"],
                review_note=serializer.validated_data.get("review_note", ""),
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(IssueAppealSerializer(reviewed).data)


class IssueAppealListView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        scoped_chama_id = self.resolve_chama_scope(request.query_params.get("chama_id"))
        membership = self.require_membership(scoped_chama_id)
        role = self._effective_role(membership, scoped_chama_id)
        queryset = IssueAppeal.objects.select_related(
            "issue", "appellant", "reviewed_by"
        ).filter(issue__chama_id=scoped_chama_id)
        if role == MembershipRole.MEMBER:
            queryset = queryset.filter(appellant=request.user)
        return Response(
            IssueAppealSerializer(queryset.order_by("-created_at"), many=True).data
        )


class IssueStatsView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = IssueStatsQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        scoped_chama_id = self.resolve_chama_scope(
            serializer.validated_data.get("chama_id")
        )
        membership = self.require_membership(scoped_chama_id)

        if not can_view_stats(request.user, membership):
            raise PermissionDenied("You are not allowed to view issue stats.")

        scoped_queryset = self._apply_role_scope(
            Issue.objects.filter(chama_id=scoped_chama_id),
            membership,
        )
        return Response(build_issue_stats(scoped_chama_id, queryset=scoped_queryset))


class SystemIssueCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not can_create_system_issue(request.user):
            raise PermissionDenied("Only system can create system issues.")

        chama_id = request.data.get("chama_id")
        trigger_type = request.data.get("trigger_type")
        title = request.data.get("title")
        description = request.data.get("description")
        category = request.data.get("category", IssueCategory.OPERATIONAL)
        severity = request.data.get("severity", IssuePriority.MEDIUM)
        linked_object_type = request.data.get("linked_object_type", "")
        linked_object_id = request.data.get("linked_object_id", "")
        metadata = request.data.get("metadata", {})

        if not all([chama_id, trigger_type, title, description]):
            raise ValidationError({"detail": "Missing required fields."})

        chama = get_object_or_404(Chama, id=chama_id)

        try:
            issue = create_system_issue(
                chama=chama,
                trigger_type=trigger_type,
                title=title,
                description=description,
                category=category,
                severity=severity,
                linked_object_type=linked_object_type,
                linked_object_id=linked_object_id,
                metadata=metadata,
            )
        except IssueServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(
            IssueDetailSerializer(issue).data,
            status=status.HTTP_201_CREATED,
        )


class IssueExportView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = IssueExportSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        scoped_chama_id = serializer.validated_data["chama_id"]
        membership = self.require_membership(scoped_chama_id)

        if not can_view_stats(request.user, membership):
            raise PermissionDenied("You are not allowed to export issues.")

        queryset = Issue.objects.filter(chama_id=scoped_chama_id)

        if serializer.validated_data.get("status"):
            queryset = queryset.filter(status=serializer.validated_data["status"])
        if serializer.validated_data.get("category"):
            queryset = queryset.filter(category=serializer.validated_data["category"])
        if serializer.validated_data.get("date_from"):
            queryset = queryset.filter(created_at__date__gte=serializer.validated_data["date_from"])
        if serializer.validated_data.get("date_to"):
            queryset = queryset.filter(created_at__date__lte=serializer.validated_data["date_to"])

        queryset = self._apply_role_scope(queryset, membership)

        issues_data = []
        for issue in queryset.select_related(
            "created_by", "assigned_to", "reported_user", "chama"
        ).prefetch_related("ratings", "resolutions"):
            avg_rating = None
            ratings = list(issue.ratings.all())
            if ratings:
                avg_rating = sum(r.score for r in ratings) / len(ratings)

            issues_data.append({
                "issue_code": issue.issue_code,
                "title": issue.title,
                "category": issue.category,
                "severity": issue.severity,
                "status": issue.status,
                "source_type": issue.source_type,
                "issue_scope": issue.issue_scope,
                "created_by": issue.created_by.full_name if issue.created_by else None,
                "assigned_to": issue.assigned_to.full_name if issue.assigned_to else None,
                "reported_user": issue.reported_user.full_name if issue.reported_user else None,
                "reported_by": issue.created_by.phone if issue.created_by else None,
                "created_at": issue.created_at.isoformat(),
                "resolved_at": issue.resolved_at.isoformat() if issue.resolved_at else None,
                "closed_at": issue.closed_at.isoformat() if issue.closed_at else None,
                "reopened_count": issue.reopened_count,
                "rating_average": avg_rating,
                "rating_count": len(ratings),
            })

        return Response({
            "chama_id": str(scoped_chama_id),
            "export_date": timezone.now().isoformat(),
            "total_issues": len(issues_data),
            "issues": issues_data,
        })
