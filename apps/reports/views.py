import hashlib
import json

from django.conf import settings
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import serializers
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)

from apps.billing.gating import require_feature
from apps.chama.models import MembershipRole
from apps.chama.permissions import get_membership
from apps.chama.services import get_effective_role
from apps.finance.models import Loan
from apps.reports.excel_export import ReportXLSXRenderer
from apps.reports.models import (
    ReportFormat,
    ReportRun,
    ReportRunStatus,
    ReportType,
    StatementDownloadHistory,
)
from apps.reports.pdf_generator import ReportPDFRenderer
from apps.reports.serializers import (
    ChamaHealthQuerySerializer,
    ChamaSummaryQuerySerializer,
    CohortAnalysisQuerySerializer,
    CollectionForecastQuerySerializer,
    DefaulterRiskQuerySerializer,
    LoanApprovalsLogQuerySerializer,
    LoanScheduleQuerySerializer,
    MemberStatementQuerySerializer,
    ReportRunSerializer,
    StatementDownloadHistorySerializer,
)
from apps.reports.services import ReportService
from apps.reports.tasks import generate_report_run
from core.schema import error_response_serializer
from core.throttles import ReportExportRateThrottle

SCHEMA_CHAMA_ID = "00000000-0000-0000-0000-000000000000"

report_generation_response = inline_serializer(
    name="ReportGenerationResponse",
    fields={
        "report_run_id": serializers.CharField(required=False),
        "id": serializers.CharField(required=False),
        "report_type": serializers.ChoiceField(choices=ReportType.choices, required=False),
        "format": serializers.ChoiceField(choices=ReportFormat.choices, required=False),
        "status": serializers.CharField(),
        "detail": serializers.CharField(required=False),
        "created_at": serializers.DateTimeField(required=False),
    },
)
report_request_serializer = inline_serializer(
    name="ReportRequestSerializer",
    fields={
        "chama_id": serializers.UUIDField(),
        "report_type": serializers.ChoiceField(choices=ReportType.choices),
        "format": serializers.ChoiceField(
            choices=ReportFormat.choices,
            required=False,
            default=ReportFormat.PDF,
        ),
        "scope": serializers.CharField(required=False, default="chama"),
        "filters": serializers.JSONField(required=False),
    },
)
report_list_query_serializer = inline_serializer(
    name="ReportListQuerySerializer",
    fields={"chama_id": serializers.UUIDField()},
)
all_reports_query_serializer = inline_serializer(
    name="AllReportsQuerySerializer",
    fields={
        "chama_id": serializers.UUIDField(),
        "status": serializers.ChoiceField(
            choices=ReportRunStatus.choices,
            required=False,
        ),
        "page": serializers.IntegerField(required=False, min_value=1, default=1),
    },
)
report_preview_query_serializer = inline_serializer(
    name="ReportPreviewQuerySerializer",
    fields={
        "chama_id": serializers.UUIDField(),
        "from_date": serializers.DateField(required=False),
        "to_date": serializers.DateField(required=False),
    },
)
report_preview_response = inline_serializer(
    name="ReportPreviewResponse",
    fields={
        "total_count": serializers.IntegerField(),
        "transactions": serializers.ListField(child=serializers.JSONField()),
        "summary": serializers.JSONField(),
    },
)
report_download_response = inline_serializer(
    name="ReportDownloadResponse",
    fields={
        "download_url": serializers.CharField(),
        "file_name": serializers.CharField(allow_blank=True),
        "file_size": serializers.IntegerField(required=False, allow_null=True),
        "status": serializers.CharField(required=False),
    },
)
report_run_summary_item = inline_serializer(
    name="ReportRunSummaryItem",
    fields={
        "id": serializers.CharField(),
        "report_type": serializers.ChoiceField(choices=ReportType.choices),
        "format": serializers.ChoiceField(choices=ReportFormat.choices),
        "status": serializers.ChoiceField(choices=ReportRunStatus.choices),
        "file_path": serializers.CharField(required=False, allow_blank=True, allow_null=True),
        "file_name": serializers.CharField(required=False, allow_blank=True, allow_null=True),
        "file_size": serializers.IntegerField(required=False, allow_null=True),
        "error_message": serializers.CharField(required=False, allow_blank=True),
        "created_at": serializers.DateTimeField(),
        "completed_at": serializers.DateTimeField(required=False, allow_null=True),
        "generated_by": serializers.CharField(required=False, allow_null=True),
    },
)
report_run_summary_response = inline_serializer(
    name="ReportRunSummaryResponse",
    fields={"results": serializers.ListField(child=serializers.JSONField())},
)
all_reports_response = inline_serializer(
    name="AllReportsResponse",
    fields={
        "results": serializers.ListField(child=serializers.JSONField()),
        "count": serializers.IntegerField(),
        "page": serializers.IntegerField(),
        "total_pages": serializers.IntegerField(),
    },
)
report_rendered_responses = {
    (200, "application/json"): OpenApiResponse(
        response=OpenApiTypes.OBJECT,
        description="Structured JSON report payload when `format=json`.",
    ),
    (200, "application/pdf"): OpenApiResponse(
        response=OpenApiTypes.BINARY,
        description="PDF file attachment when `format=pdf`.",
    ),
    (
        200,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ): OpenApiResponse(
        response=OpenApiTypes.BINARY,
        description="Excel file attachment when `format=xlsx`.",
    ),
    202: report_generation_response,
    400: error_response_serializer(name="ReportRequestError"),
}


def _cache_key(prefix: str, payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"reports:{prefix}:{digest}"


def _ttl_seconds() -> int:
    return int(getattr(settings, "REPORT_CACHE_TTL_SECONDS", 300))


def _require_membership(user, chama_id):
    membership = get_membership(user, chama_id)
    if not membership:
        raise PermissionDenied("You are not an approved active member of this chama.")
    return membership


def _membership_role(user, membership) -> str | None:
    if not membership:
        return None
    return get_effective_role(user, membership.chama_id, membership)


def _is_auditor(user, membership) -> bool:
    return _membership_role(user, membership) == MembershipRole.AUDITOR


class _BaseReportView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ReportExportRateThrottle]
    report_type: str | None = None

    def _create_report_run(
        self, *, request, chama_id, format_value, parameters, is_async
    ):
        return ReportRun.objects.create(
            chama_id=chama_id,
            report_type=self.report_type,
            format=format_value,
            status=ReportRunStatus.PENDING,
            parameters=parameters,
            generated_by=request.user,
            is_async=is_async,
            created_by=request.user,
            updated_by=request.user,
        )

    def _mark_run_success(self, report_run: ReportRun, payload: dict):
        report_run.status = ReportRunStatus.COMPLETED
        report_run.result = payload
        report_run.updated_by = report_run.generated_by
        report_run.save(update_fields=["status", "result", "updated_by", "updated_at"])

    def _mark_run_failed(self, report_run: ReportRun, error_message: str):
        report_run.status = ReportRunStatus.FAILED
        report_run.error_message = error_message
        report_run.updated_by = report_run.generated_by
        report_run.save(
            update_fields=[
                "status",
                "error_message",
                "updated_by",
                "updated_at",
            ]
        )

    def _render_download(self, *, payload, format_value, filename, watermark=False):
        if format_value == ReportFormat.JSON:
            return Response(payload, status=status.HTTP_200_OK)

        if self.report_type in {ReportType.MEMBER_STATEMENT, ReportType.LOAN_STATEMENT}:
            pdf_builder = ReportPDFRenderer.render_member_statement
            xlsx_builder = ReportXLSXRenderer.render_member_statement
        elif self.report_type in {
            ReportType.CHAMA_SUMMARY,
            ReportType.LOAN_MONTHLY_SUMMARY,
            ReportType.CHAMA_HEALTH_SCORE,
            ReportType.COLLECTION_FORECAST,
            ReportType.DEFAULTER_RISK,
        }:
            pdf_builder = ReportPDFRenderer.render_chama_summary
            xlsx_builder = ReportXLSXRenderer.render_chama_summary
        elif self.report_type == ReportType.LOAN_SCHEDULE:
            pdf_builder = ReportPDFRenderer.render_loan_schedule
            xlsx_builder = ReportXLSXRenderer.render_loan_schedule
        elif self.report_type == ReportType.LOAN_APPROVALS_LOG:
            pdf_builder = ReportPDFRenderer.render_loan_approvals_log
            xlsx_builder = ReportXLSXRenderer.render_loan_approvals_log
        else:
            return Response(
                {"detail": "Unsupported report renderer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if format_value == ReportFormat.PDF:
            data = pdf_builder(payload, watermark=watermark)
            response = HttpResponse(data, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
            return response

        if format_value == ReportFormat.XLSX:
            data = xlsx_builder(payload, watermark=watermark)
            response = HttpResponse(
                data,
                content_type=(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ),
            )
            response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
            return response

        return Response(
            {"detail": "Unsupported format."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def _record_download(
        self,
        *,
        request,
        chama_id,
        format_value,
        filename,
        month=None,
        year=None,
    ):
        StatementDownloadHistory.objects.create(
            user=request.user,
            chama_id=chama_id,
            report_type=self.report_type,
            format=format_value,
            file_name=filename,
            period_month=month,
            period_year=year,
            created_by=request.user,
            updated_by=request.user,
        )

    def _execute(self, *, request, chama_id, format_value, async_mode, parameters):
        report_run = self._create_report_run(
            request=request,
            chama_id=chama_id,
            format_value=format_value,
            parameters=parameters,
            is_async=async_mode,
        )

        if async_mode:
            try:
                generate_report_run.delay(str(report_run.id))
            except Exception as exc:  # noqa: BLE001
                self._mark_run_failed(report_run, str(exc))
                return None, Response(
                    {"detail": "Failed to queue report generation."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return report_run, Response(
                {
                    "report_run_id": str(report_run.id),
                    "status": report_run.status,
                    "detail": "Report generation queued.",
                },
                status=status.HTTP_202_ACCEPTED,
            )

        cache_key = _cache_key(self.report_type, parameters)
        payload = cache.get(cache_key)
        if payload is None:
            try:
                payload = ReportService.build_report_payload(
                    report_type=self.report_type,
                    parameters=parameters,
                )
            except Exception as exc:  # noqa: BLE001
                self._mark_run_failed(report_run, str(exc))
                raise
            cache.set(cache_key, payload, timeout=_ttl_seconds())

        self._mark_run_success(report_run, payload)
        return payload, None


class MemberStatementReportView(_BaseReportView):
    report_type = ReportType.MEMBER_STATEMENT

    @extend_schema(
        tags=["Reports"],
        operation_id="retrieve_member_statement_report",
        parameters=[MemberStatementQuerySerializer],
        responses=report_rendered_responses,
    )
    def get(self, request):
        query = MemberStatementQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data

        chama_id = str(data["chama_id"])
        membership = _require_membership(request.user, chama_id)
        role = _membership_role(request.user, membership)

        member_id = str(data.get("member_id") or request.user.id)
        if role == MembershipRole.MEMBER and member_id != str(request.user.id):
            raise PermissionDenied("Members can only view their own statements.")

        if member_id != str(request.user.id) and role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
        }:
            raise PermissionDenied(
                "Only treasurer, admin, or auditor can view other members' statements."
            )

        from_date = data.get("from")
        to_date = data.get("to")
        format_value = data.get("format", ReportFormat.JSON)
        async_mode = data.get("async", False)
        watermark = bool(
            data.get("watermark", False) or _is_auditor(request.user, membership)
        )

        parameters = {
            "chama_id": chama_id,
            "member_id": member_id,
            "from_date": from_date.isoformat() if from_date else None,
            "to_date": to_date.isoformat() if to_date else None,
        }

        payload, async_response = self._execute(
            request=request,
            chama_id=chama_id,
            format_value=format_value,
            async_mode=async_mode,
            parameters=parameters,
        )
        if async_response:
            return async_response

        filename = f"member_statement_{chama_id}_{member_id}"
        self._record_download(
            request=request,
            chama_id=chama_id,
            format_value=format_value,
            filename=filename,
            month=from_date.month if from_date else None,
            year=from_date.year if from_date else None,
        )
        return self._render_download(
            payload=payload,
            format_value=format_value,
            filename=filename,
            watermark=watermark,
        )


class LoanStatementReportView(MemberStatementReportView):
    report_type = ReportType.LOAN_STATEMENT

    @extend_schema(
        tags=["Reports"],
        operation_id="retrieve_loan_statement_report",
        parameters=[MemberStatementQuerySerializer],
        responses=report_rendered_responses,
    )
    def get(self, request):
        return super().get(request)


class ChamaSummaryReportView(_BaseReportView):
    report_type = ReportType.CHAMA_SUMMARY

    @extend_schema(
        tags=["Reports"],
        operation_id="retrieve_chama_summary_report",
        parameters=[ChamaSummaryQuerySerializer],
        responses=report_rendered_responses,
    )
    def get(self, request):
        query = ChamaSummaryQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data

        chama_id = str(data["chama_id"])
        membership = _require_membership(request.user, chama_id)
        role = _membership_role(request.user, membership)

        if role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
        }:
            raise PermissionDenied(
                "Only treasurer, admin, or auditor can view chama summary reports."
            )

        month = data["month"]
        year = data["year"]
        format_value = data.get("format", ReportFormat.JSON)
        async_mode = data.get("async", False)
        watermark = bool(
            data.get("watermark", False) or _is_auditor(request.user, membership)
        )

        parameters = {
            "chama_id": chama_id,
            "month": month,
            "year": year,
        }

        payload, async_response = self._execute(
            request=request,
            chama_id=chama_id,
            format_value=format_value,
            async_mode=async_mode,
            parameters=parameters,
        )
        if async_response:
            return async_response

        filename = f"chama_summary_{chama_id}_{year}_{month:02d}"
        self._record_download(
            request=request,
            chama_id=chama_id,
            format_value=format_value,
            filename=filename,
            month=month,
            year=year,
        )
        return self._render_download(
            payload=payload,
            format_value=format_value,
            filename=filename,
            watermark=watermark,
        )


class LoanMonthlySummaryReportView(ChamaSummaryReportView):
    report_type = ReportType.LOAN_MONTHLY_SUMMARY

    @extend_schema(
        tags=["Reports"],
        operation_id="retrieve_loan_monthly_summary_report",
        parameters=[ChamaSummaryQuerySerializer],
        responses=report_rendered_responses,
    )
    def get(self, request):
        return super().get(request)


class LoanScheduleReportView(_BaseReportView):
    report_type = ReportType.LOAN_SCHEDULE

    @extend_schema(
        tags=["Reports"],
        operation_id="retrieve_loan_schedule_report",
        parameters=[LoanScheduleQuerySerializer],
        responses=report_rendered_responses,
    )
    def get(self, request):
        query = LoanScheduleQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data

        chama_id = str(data["chama_id"])
        membership = _require_membership(request.user, chama_id)
        role = _membership_role(request.user, membership)

        if role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
            MembershipRole.MEMBER,
        }:
            raise PermissionDenied("You are not allowed to view loan schedules.")
        if role == MembershipRole.MEMBER:
            owns_loan = Loan.objects.filter(
                id=data["loan_id"],
                chama_id=chama_id,
                member=request.user,
            ).exists()
            if not owns_loan:
                raise PermissionDenied(
                    "Members can only view their own loan schedules."
                )

        format_value = data.get("format", ReportFormat.JSON)
        async_mode = data.get("async", False)
        watermark = bool(
            data.get("watermark", False) or _is_auditor(request.user, membership)
        )
        parameters = {
            "chama_id": chama_id,
            "loan_id": str(data["loan_id"]),
        }

        payload, async_response = self._execute(
            request=request,
            chama_id=chama_id,
            format_value=format_value,
            async_mode=async_mode,
            parameters=parameters,
        )
        if async_response:
            return async_response

        filename = f"loan_schedule_{data['loan_id']}"
        self._record_download(
            request=request,
            chama_id=chama_id,
            format_value=format_value,
            filename=filename,
        )
        return self._render_download(
            payload=payload,
            format_value=format_value,
            filename=filename,
            watermark=watermark,
        )


class LoanApprovalsLogReportView(_BaseReportView):
    report_type = ReportType.LOAN_APPROVALS_LOG

    @extend_schema(
        tags=["Reports"],
        operation_id="retrieve_loan_approvals_log_report",
        parameters=[LoanApprovalsLogQuerySerializer],
        responses=report_rendered_responses,
    )
    def get(self, request):
        query = LoanApprovalsLogQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data

        chama_id = str(data["chama_id"])
        membership = _require_membership(request.user, chama_id)
        role = _membership_role(request.user, membership)
        if role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
        }:
            raise PermissionDenied("You are not allowed to view loan approvals log.")

        format_value = data.get("format", ReportFormat.JSON)
        async_mode = data.get("async", False)
        watermark = bool(
            data.get("watermark", False) or _is_auditor(request.user, membership)
        )
        parameters = {
            "chama_id": chama_id,
            "month": data.get("month"),
            "year": data.get("year"),
        }

        payload, async_response = self._execute(
            request=request,
            chama_id=chama_id,
            format_value=format_value,
            async_mode=async_mode,
            parameters=parameters,
        )
        if async_response:
            return async_response

        suffix = ""
        if data.get("month") and data.get("year"):
            suffix = f"_{data['year']}_{data['month']:02d}"
        filename = f"loan_approvals_log_{chama_id}{suffix}"
        self._record_download(
            request=request,
            chama_id=chama_id,
            format_value=format_value,
            filename=filename,
            month=data.get("month"),
            year=data.get("year"),
        )
        return self._render_download(
            payload=payload,
            format_value=format_value,
            filename=filename,
            watermark=watermark,
        )


class ChamaHealthScoreView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ChamaHealthQuerySerializer

    @require_feature('advanced_reports')
    @extend_schema(
        tags=["Reports"],
        operation_id="retrieve_chama_health_score",
        parameters=[ChamaHealthQuerySerializer],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        query = ChamaHealthQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        chama_id = str(query.validated_data["chama_id"])
        membership = _require_membership(request.user, chama_id)
        role = _membership_role(request.user, membership)
        if role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
            MembershipRole.SECRETARY,
        }:
            raise PermissionDenied("You are not allowed to view chama health score.")
        return Response(ReportService.build_chama_health_score(chama_id=chama_id))


class CollectionForecastView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CollectionForecastQuerySerializer

    @require_feature('advanced_reports')
    @extend_schema(
        tags=["Reports"],
        operation_id="retrieve_collection_forecast",
        parameters=[CollectionForecastQuerySerializer],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        query = CollectionForecastQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        chama_id = str(query.validated_data["chama_id"])
        membership = _require_membership(request.user, chama_id)
        role = _membership_role(request.user, membership)
        if role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
        }:
            raise PermissionDenied("You are not allowed to view collection forecast.")
        return Response(
            ReportService.build_collection_forecast(
                chama_id=chama_id,
                months=query.validated_data["months"],
            )
        )


class DefaulterRiskView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = DefaulterRiskQuerySerializer

    @require_feature('advanced_reports')
    @extend_schema(
        tags=["Reports"],
        operation_id="retrieve_defaulter_risk_report",
        parameters=[DefaulterRiskQuerySerializer],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        query = DefaulterRiskQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        chama_id = str(query.validated_data["chama_id"])
        membership = _require_membership(request.user, chama_id)
        role = _membership_role(request.user, membership)
        if role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
            MembershipRole.SECRETARY,
        }:
            raise PermissionDenied("You are not allowed to view defaulter risk report.")
        return Response(ReportService.build_defaulter_risk(chama_id=chama_id))


class CohortAnalysisView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ReportExportRateThrottle]
    serializer_class = CohortAnalysisQuerySerializer

    @require_feature('advanced_reports')
    @extend_schema(
        tags=["Reports"],
        operation_id="retrieve_cohort_analysis_report",
        parameters=[CohortAnalysisQuerySerializer],
        responses={
            200: OpenApiTypes.OBJECT,
            400: error_response_serializer(name="CohortAnalysisError"),
        },
    )
    def get(self, request):
        query = CohortAnalysisQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data

        chama_id = str(data["chama_id"])
        membership = _require_membership(request.user, chama_id)
        role = _membership_role(request.user, membership)
        if role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
            MembershipRole.SECRETARY,
        }:
            raise PermissionDenied("You are not allowed to view cohort analysis.")

        if data.get("format", ReportFormat.JSON) != ReportFormat.JSON:
            return Response(
                {"detail": ("Cohort analysis export is currently JSON only.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = ReportService.build_member_cohort_analysis(
            chama_id=chama_id,
            months=data["months"],
        )
        return Response(payload, status=status.HTTP_200_OK)


@extend_schema_view(
    get=extend_schema(
        tags=["Reports"],
        operation_id="list_report_runs",
        parameters=[report_list_query_serializer],
        responses={200: ReportRunSerializer(many=True)},
    )
)
class ReportRunListView(generics.ListAPIView):
    serializer_class = ReportRunSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ReportRun.objects.none()
        chama_id = self.request.query_params.get("chama_id")
        if not chama_id:
            raise PermissionDenied("chama_id is required.")

        membership = _require_membership(self.request.user, chama_id)
        role = _membership_role(self.request.user, membership)
        if role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
        }:
            raise PermissionDenied("You are not allowed to view report runs.")

        return ReportRun.objects.select_related("generated_by").filter(
            chama_id=chama_id
        )


@extend_schema_view(
    get=extend_schema(
        tags=["Reports"],
        operation_id="retrieve_report_run",
        responses={200: ReportRunSerializer},
    )
)
class ReportRunDetailView(generics.RetrieveAPIView):
    serializer_class = ReportRunSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ReportRun.objects.none()
        queryset = ReportRun.objects.select_related("generated_by", "chama")
        report_id = self.kwargs["id"]
        report = get_object_or_404(queryset, id=report_id)
        membership = _require_membership(self.request.user, str(report.chama_id))
        role = _membership_role(self.request.user, membership)
        if role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
        }:
            raise PermissionDenied("You are not allowed to view this report run.")
        return queryset.filter(id=report.id)


@extend_schema_view(
    get=extend_schema(
        tags=["Reports"],
        operation_id="list_statement_download_history",
        parameters=[report_list_query_serializer],
        responses={200: StatementDownloadHistorySerializer(many=True)},
    )
)
class StatementDownloadHistoryView(generics.ListAPIView):
    serializer_class = StatementDownloadHistorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return StatementDownloadHistory.objects.none()
        chama_id = self.request.query_params.get("chama_id")
        if not chama_id:
            raise PermissionDenied("chama_id is required.")
        membership = _require_membership(self.request.user, chama_id)
        role = _membership_role(self.request.user, membership)
        queryset = StatementDownloadHistory.objects.select_related(
            "user", "chama"
        ).filter(chama_id=chama_id)
        if role == MembershipRole.MEMBER:
            queryset = queryset.filter(user=self.request.user)
        return queryset.order_by("-created_at")


# ==========================================
# New API Endpoints for Flutter Reports Service
# ==========================================


class ReportRequestView(APIView):
    """
    Request async report generation.
    POST /api/v1/reports/request/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = report_request_serializer

    @extend_schema(
        tags=["Reports"],
        operation_id="create_report_request",
        request=report_request_serializer,
        responses={
            201: report_generation_response,
            400: error_response_serializer(name="CreateReportRequestError"),
        },
    )
    def post(self, request):
        chama_id = request.data.get("chama_id")
        if not chama_id:
            return Response(
                {"error": "chama_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate membership
        membership = _require_membership(request.user, chama_id)
        role = _membership_role(request.user, membership)
        scope = request.data.get("scope", "chama")

        # Validate report type
        report_type = request.data.get("report_type")
        if not report_type:
            return Response(
                {"error": "report_type is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate format
        format_value = request.data.get("format", "pdf")
        if format_value not in {f.value for f in ReportFormat}:
            return Response(
                {"error": f"Invalid format. Valid: {', '.join(f.value for f in ReportFormat)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check permissions based on scope
        if scope == "chama" and role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
        }:
            raise PermissionDenied("You need admin/treasurer/auditor role to generate chama reports.")

        # Create report run
        parameters = request.data.get("filters", {})
        report_run = ReportRun.objects.create(
            chama_id=chama_id,
            report_type=report_type,
            format=format_value,
            status=ReportRunStatus.PENDING,
            parameters=parameters,
            generated_by=request.user,
            is_async=True,
            created_by=request.user,
            updated_by=request.user,
        )

        # Trigger async generation
        generate_report_run.delay(str(report_run.id))

        return Response({
            "id": report_run.id,
            "report_type": report_run.report_type,
            "format": report_run.format,
            "status": report_run.status,
            "created_at": report_run.created_at.isoformat(),
        }, status=status.HTTP_201_CREATED)


class MyReportsView(APIView):
    """
    Get current user's report requests.
    GET /api/v1/reports/my/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = report_list_query_serializer

    @extend_schema(
        tags=["Reports"],
        operation_id="list_my_report_requests",
        parameters=[report_list_query_serializer],
        responses={200: report_run_summary_response},
    )
    def get(self, request):
        chama_id = request.query_params.get("chama_id")
        if not chama_id:
            return Response(
                {"error": "chama_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate membership
        _require_membership(request.user, chama_id)

        # Get user's reports for this chama
        reports = ReportRun.objects.filter(
            chama_id=chama_id,
            generated_by=request.user,
        ).order_by("-created_at")[:50]

        results = []
        for r in reports:
            results.append({
                "id": r.id,
                "report_type": r.report_type,
                "format": r.format,
                "status": r.status,
                "file_path": r.file_path,
                "file_name": r.file_name,
                "file_size": r.file_size,
                "error_message": r.error_message,
                "created_at": r.created_at.isoformat(),
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            })

        return Response({"results": results})


class AllReportsView(APIView):
    """
    Get all report requests (admin/treasurer/auditor).
    GET /api/v1/reports/all/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = all_reports_query_serializer

    @extend_schema(
        tags=["Reports"],
        operation_id="list_all_report_requests",
        parameters=[all_reports_query_serializer],
        responses={200: all_reports_response},
    )
    def get(self, request):
        chama_id = request.query_params.get("chama_id")
        if not chama_id:
            return Response(
                {"error": "chama_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        membership = _require_membership(request.user, chama_id)
        role = _membership_role(request.user, membership)

        if role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
        }:
            raise PermissionDenied("Only admins, treasurers, and auditors can view all reports.")

        queryset = ReportRun.objects.filter(chama_id=chama_id)

        # Filter by status
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # Pagination
        page = int(request.query_params.get("page", 1))
        page_size = 20
        start = (page - 1) * page_size
        end = start + page_size
        total = queryset.count()
        reports = queryset.order_by("-created_at")[start:end]

        results = []
        for r in reports:
            results.append({
                "id": r.id,
                "report_type": r.report_type,
                "format": r.format,
                "status": r.status,
                "file_path": r.file_path,
                "file_name": r.file_name,
                "file_size": r.file_size,
                "error_message": r.error_message,
                "created_at": r.created_at.isoformat(),
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "generated_by": r.generated_by.username if r.generated_by else None,
            })

        return Response({
            "results": results,
            "count": total,
            "page": page,
            "total_pages": (total + page_size - 1) // page_size,
        })


class ReportPreviewView(APIView):
    """
    Get fast preview data for a report.
    GET /api/v1/reports/preview/<report_type>/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = report_preview_query_serializer

    @extend_schema(
        tags=["Reports"],
        operation_id="preview_report_data",
        parameters=[report_preview_query_serializer],
        responses={
            200: report_preview_response,
            400: error_response_serializer(name="ReportPreviewError"),
        },
    )
    def get(self, request, report_type):
        chama_id = request.query_params.get("chama_id")
        if not chama_id:
            return Response(
                {"error": "chama_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        membership = _require_membership(request.user, chama_id)
        _membership_role(request.user, membership)

        # Parse date filters
        from_date = request.query_params.get("from_date")
        to_date = request.query_params.get("to_date")

        try:
            from apps.reports.services import ReportService
            
            # Get preview data based on report type
            if report_type == ReportType.MEMBER_STATEMENT:
                data = ReportService.get_member_statement(
                    chama_id=chama_id,
                    user=request.user,
                    from_date=from_date,
                    to_date=to_date,
                )
            elif report_type == ReportType.CHAMA_SUMMARY:
                data = ReportService.get_chama_summary(
                    chama_id=chama_id,
                    from_date=from_date,
                    to_date=to_date,
                )
            elif report_type == ReportType.LOAN_MONTHLY_SUMMARY:
                data = ReportService.get_loan_monthly_summary(
                    chama_id=chama_id,
                    from_date=from_date,
                    to_date=to_date,
                )
            else:
                return Response(
                    {"error": f"Preview not available for {report_type}"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Extract summary
            summary = data.get("summary", {})
            transactions = data.get("transactions", [])[:100]  # Limit preview

            return Response({
                "total_count": len(transactions),
                "transactions": transactions,
                "summary": summary,
            })

        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ReportDownloadView(APIView):
    """
    Get download URL for a report.
    GET /api/v1/reports/download/<report_id>/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = report_download_response

    @extend_schema(
        tags=["Reports"],
        operation_id="retrieve_report_download",
        responses={
            200: report_download_response,
            400: error_response_serializer(name="ReportDownloadError"),
        },
    )
    def get(self, request, report_id):
        report = get_object_or_404(ReportRun, id=report_id)
        membership = _require_membership(request.user, str(report.chama_id))
        role = _membership_role(request.user, membership)

        # Check if user can access this report
        if report.generated_by != request.user and role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
        }:
            raise PermissionDenied("You cannot access this report.")

        if report.status != ReportRunStatus.COMPLETED:
            return Response(
                {"error": "Report is not ready yet", "status": report.status},
                status=status.HTTP_400_BAD_REQUEST
            )

        download_url = (
            default_storage.url(report.file_path)
            if report.file_path
            else f"/media/reports/{report.file_name}"
        )

        return Response({
            "download_url": download_url,
            "file_name": report.file_name,
            "file_size": report.file_size,
        })
