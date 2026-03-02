import uuid

from django.db.models import Q
from django.shortcuts import get_object_or_404
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
    IssueAttachment,
    IssueCategory,
    IssueComment,
    IssueMediationNote,
    IssueStatus,
)
from apps.issues.permissions import (
    can_comment_issue,
    can_moderate_issue,
    can_reopen_issue,
    can_view_issue,
    get_issue_membership,
)
from apps.issues.serializers import (
    INTERNAL_NOTE_ROLES,
    IssueAppealCreateSerializer,
    IssueAppealReviewSerializer,
    IssueAppealSerializer,
    IssueAssignSerializer,
    IssueAttachmentCreateSerializer,
    IssueAttachmentSerializer,
    IssueCommentCreateSerializer,
    IssueCommentSerializer,
    IssueCreateSerializer,
    IssueDetailSerializer,
    IssueEscalationSerializer,
    IssueFilterSerializer,
    IssueLiftSuspensionSerializer,
    IssueListSerializer,
    IssueMediationNoteCreateSerializer,
    IssueMediationNoteSerializer,
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
    assign_issue,
    build_issue_stats,
    change_issue_status,
    create_issue_appeal,
    create_mediation_note,
    escalate_issue_ladder,
    issue_warning,
    lift_user_suspension,
    log_issue_activity,
    review_issue_appeal,
    suspend_reported_user,
)
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


class IssueListCreateView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method == "POST":
            return [IssueCreateRateThrottle()]
        return super().get_throttles()

    def _apply_role_scope(self, queryset, membership):
        if self.request.user.is_superuser:
            return queryset
        if not membership:
            return queryset.none()
        role = get_effective_role(self.request.user, membership.chama_id, membership)

        if role in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.SECRETARY,
            MembershipRole.AUDITOR,
        }:
            return queryset

        if role == MembershipRole.TREASURER:
            return queryset.filter(
                Q(category=IssueCategory.FINANCE)
                | Q(created_by=self.request.user)
                | Q(reported_user=self.request.user)
            )

        return queryset.filter(
            Q(created_by=self.request.user) | Q(reported_user=self.request.user)
        )

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
            .prefetch_related("comments", "attachments")
            .filter(chama_id=scoped_chama_id)
        )
        queryset = self._apply_role_scope(queryset, membership)

        filters = filters_serializer.validated_data
        if filters.get("status"):
            queryset = queryset.filter(status=filters["status"])
        if filters.get("category"):
            queryset = queryset.filter(category=filters["category"])
        if filters.get("priority"):
            queryset = queryset.filter(priority=filters["priority"])
        if filters.get("assigned_to"):
            queryset = queryset.filter(assigned_to_id=filters["assigned_to"])
        if filters.get("reported_user"):
            queryset = queryset.filter(reported_user_id=filters["reported_user"])
        if filters.get("loan_id"):
            queryset = queryset.filter(loan_id=filters["loan_id"])
        if filters.get("created_by"):
            queryset = queryset.filter(created_by_id=filters["created_by"])
        if filters.get("date_from"):
            queryset = queryset.filter(created_at__date__gte=filters["date_from"])
        if filters.get("date_to"):
            queryset = queryset.filter(created_at__date__lte=filters["date_to"])

        search = filters.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search)
                | Q(description__icontains=search)
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
        issue = Issue.objects.create(
            chama=chama,
            title=serializer.validated_data["title"],
            description=serializer.validated_data["description"],
            category=serializer.validated_data.get("category", IssueCategory.OTHER),
            priority=serializer.validated_data.get("priority", "medium"),
            reported_user_id=serializer.validated_data.get("reported_user_id"),
            loan_id=serializer.validated_data.get("loan_id"),
            report_type=serializer.validated_data.get("report_type", ""),
            is_anonymous=serializer.validated_data.get("is_anonymous", False),
            due_at=serializer.validated_data.get("due_at"),
            created_by=request.user,
            updated_by=request.user,
        )
        log_issue_activity(
            issue,
            request.user,
            IssueActivityAction.CREATED,
            {
                "category": issue.category,
                "priority": issue.priority,
                "reported_user_id": (
                    str(issue.reported_user_id) if issue.reported_user_id else None
                ),
                "loan_id": str(issue.loan_id) if issue.loan_id else None,
            },
        )

        try:
            from apps.issues.tasks import issues_auto_triage_ai

            issues_auto_triage_ai.delay(str(issue.id))
        except Exception:  # noqa: BLE001
            # Issue creation should not fail because of async AI triage dispatch.
            pass

        return Response(
            self.serialize_issue(issue, membership).data,
            status=status.HTTP_201_CREATED,
        )


class IssueDetailView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        return Response(self.serialize_issue(issue, membership).data)

    def patch(self, request, id):
        issue = get_object_or_404(Issue, id=id)
        membership = self.require_membership(str(issue.chama_id))
        if not request.user.is_superuser and issue.created_by_id != request.user.id:
            raise PermissionDenied("Only the issue creator can edit this issue.")
        if not request.user.is_superuser and issue.status != IssueStatus.OPEN:
            raise ValidationError(
                {"detail": "Issue can only be edited while status is OPEN."}
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
            "priority",
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


class IssueCommentCreateView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_comment_issue(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to comment on this issue.")

        serializer = IssueCommentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        is_internal = serializer.validated_data.get("is_internal", False)
        if (
            is_internal
            and not request.user.is_superuser
            and getattr(membership, "role", "") not in INTERNAL_NOTE_ROLES
        ):
            raise PermissionDenied(
                "Only admin, secretary, or treasurer can add internal notes."
            )

        comment = IssueComment.objects.create(
            issue=issue,
            author=request.user,
            message=serializer.validated_data["message"],
            is_internal=is_internal,
            created_by=request.user,
            updated_by=request.user,
        )
        log_issue_activity(
            issue,
            request.user,
            IssueActivityAction.COMMENT_ADDED,
            {
                "comment_id": str(comment.id),
                "is_internal": comment.is_internal,
            },
        )

        return Response(
            IssueCommentSerializer(comment).data,
            status=status.HTTP_201_CREATED,
        )


class IssueAttachmentCreateView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_comment_issue(request.user, membership, issue):
            raise PermissionDenied(
                "You are not allowed to upload evidence on this issue."
            )

        serializer = IssueAttachmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        attachment = IssueAttachment.objects.create(
            issue=issue,
            uploaded_by=request.user,
            file=serializer.validated_data["file"],
            created_by=request.user,
            updated_by=request.user,
        )
        log_issue_activity(
            issue,
            request.user,
            IssueActivityAction.ATTACHMENT_ADDED,
            {
                "attachment_id": str(attachment.id),
                "filename": attachment.file.name,
                "content_type": attachment.content_type,
                "size": attachment.size,
            },
        )

        return Response(
            IssueAttachmentSerializer(attachment).data,
            status=status.HTTP_201_CREATED,
        )


class IssueAssignView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_moderate_issue(request.user, membership, issue):
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
            note=serializer.validated_data.get("note", ""),
        )

        return Response(self.serialize_issue(updated_issue, membership).data)


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


class IssueReopenView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        if not can_reopen_issue(request.user, membership, issue):
            raise PermissionDenied("You are not allowed to reopen this issue.")

        if issue.status not in {
            IssueStatus.CLOSED,
            IssueStatus.RESOLVED,
            IssueStatus.REJECTED,
        }:
            raise ValidationError(
                {"detail": "Only closed, resolved, or rejected issues can be reopened."}
            )

        note = request.data.get("note", "") if hasattr(request.data, "get") else ""
        try:
            updated_issue = change_issue_status(
                issue,
                IssueStatus.REOPENED,
                request.user,
                note=note,
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
        if not request.user.is_superuser and (
            not membership
            or role
            not in {
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.SECRETARY,
            }
        ):
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
        role = self._effective_role(membership, str(issue.chama_id))
        if not request.user.is_superuser and (
            not membership
            or role
            not in {
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.SECRETARY,
            }
        ):
            raise PermissionDenied("Only admin/secretary can suspend users.")

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
        role = self._effective_role(membership, str(issue.chama_id))
        if not request.user.is_superuser and (
            not membership
            or role
            not in {
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.SECRETARY,
            }
        ):
            raise PermissionDenied("Only admin/secretary can lift suspensions.")

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


class IssueEscalateView(IssueScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IssueModerationRateThrottle]

    def post(self, request, id):
        issue, membership = self.get_issue_and_membership(id)
        role = self._effective_role(membership, str(issue.chama_id))
        if not request.user.is_superuser and (
            not membership
            or role
            not in {
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.SECRETARY,
            }
        ):
            raise PermissionDenied("Only admin/secretary can escalate issues.")

        serializer = IssueEscalationSerializer(data=request.data)
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
        role = self._effective_role(membership, scoped_chama_id)

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
            raise PermissionDenied("You are not allowed to view issue stats.")

        return Response(build_issue_stats(scoped_chama_id))
