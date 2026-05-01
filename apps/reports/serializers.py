from rest_framework import serializers

from apps.reports.models import ReportFormat, ReportRun, StatementDownloadHistory


class MemberStatementQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    member_id = serializers.UUIDField(required=False)
    from_date = serializers.DateField(required=False, source="from")
    to_date = serializers.DateField(required=False, source="to")
    format = serializers.ChoiceField(
        choices=ReportFormat.choices,
        required=False,
        default=ReportFormat.JSON,
    )
    watermark = serializers.BooleanField(required=False, default=False)
    async_mode = serializers.BooleanField(required=False, default=False, source="async")

    def validate(self, attrs):
        start = attrs.get("from")
        end = attrs.get("to")
        if start and end and end < start:
            raise serializers.ValidationError({"to": "to cannot be before from."})
        return attrs


class ChamaSummaryQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    month = serializers.IntegerField(min_value=1, max_value=12)
    year = serializers.IntegerField(min_value=2000, max_value=2100)
    format = serializers.ChoiceField(
        choices=ReportFormat.choices,
        required=False,
        default=ReportFormat.JSON,
    )
    watermark = serializers.BooleanField(required=False, default=False)
    async_mode = serializers.BooleanField(required=False, default=False, source="async")


class LoanScheduleQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    loan_id = serializers.UUIDField()
    format = serializers.ChoiceField(
        choices=ReportFormat.choices,
        required=False,
        default=ReportFormat.JSON,
    )
    watermark = serializers.BooleanField(required=False, default=False)
    async_mode = serializers.BooleanField(required=False, default=False, source="async")


class LoanApprovalsLogQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    month = serializers.IntegerField(required=False, min_value=1, max_value=12)
    year = serializers.IntegerField(required=False, min_value=2000, max_value=2100)
    format = serializers.ChoiceField(
        choices=ReportFormat.choices,
        required=False,
        default=ReportFormat.JSON,
    )
    watermark = serializers.BooleanField(required=False, default=False)
    async_mode = serializers.BooleanField(required=False, default=False, source="async")

    def validate(self, attrs):
        month = attrs.get("month")
        year = attrs.get("year")
        if (month and not year) or (year and not month):
            raise serializers.ValidationError(
                "month and year must be provided together."
            )
        return attrs


class ChamaHealthQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()


class CollectionForecastQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    months = serializers.IntegerField(
        required=False, min_value=1, max_value=12, default=3
    )


class DefaulterRiskQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()


class CohortAnalysisQuerySerializer(serializers.Serializer):
    chama_id = serializers.UUIDField()
    months = serializers.IntegerField(
        required=False, min_value=1, max_value=24, default=6
    )
    format = serializers.ChoiceField(
        choices=ReportFormat.choices,
        required=False,
        default=ReportFormat.JSON,
    )
    watermark = serializers.BooleanField(required=False, default=False)
    async_mode = serializers.BooleanField(required=False, default=False, source="async")


class ReportRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportRun
        fields = [
            "id",
            "chama",
            "report_type",
            "format",
            "status",
            "parameters",
            "result",
            "generated_by",
            "is_async",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class StatementDownloadHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = StatementDownloadHistory
        fields = [
            "id",
            "user",
            "chama",
            "report_type",
            "format",
            "file_name",
            "period_month",
            "period_year",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
