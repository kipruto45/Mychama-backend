# Exports Module Serializers

from rest_framework import serializers

from .models import ExportField, ExportJob, ExportPermission, ScheduledExport


class ExportFieldSerializer(serializers.ModelSerializer):
    """Serializer for ExportField"""
    class Meta:
        model = ExportField
        fields = ['id', 'dataset_type', 'field_name', 'field_label', 'field_type', 'is_sensitive', 'is_default', 'description']


class ExportPermissionSerializer(serializers.ModelSerializer):
    """Serializer for ExportPermission"""
    class Meta:
        model = ExportPermission
        fields = ['id', 'chama', 'role', 'allowed_datasets', 'can_schedule', 'can_view_all']


class ExportJobSerializer(serializers.ModelSerializer):
    """Serializer for ExportJob"""
    dataset_type_display = serializers.CharField(source='get_dataset_type_display', read_only=True)
    export_format_display = serializers.CharField(source='get_export_format_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    requested_by_name = serializers.SerializerMethodField()
    file_size_mb = serializers.SerializerMethodField()
    
    class Meta:
        model = ExportJob
        fields = [
            'id', 'chama', 'dataset_type', 'dataset_type_display',
            'fields', 'filters', 'start_date', 'end_date',
            'export_format', 'export_format_display', 'status', 'status_display',
            'file_url', 'file_size', 'file_size_mb', 'record_count',
            'error_message', 'expires_at',
            'requested_by', 'requested_by_name',
            'created_at', 'started_at', 'completed_at'
        ]
        read_only_fields = ['created_at', 'started_at', 'completed_at']
    
    def get_requested_by_name(self, obj):
        if obj.requested_by:
            return obj.requested_by.get_full_name()
        return None
    
    def get_file_size_mb(self, obj):
        if obj.file_size:
            return round(obj.file_size / (1024 * 1024), 2)
        return None


class ExportJobCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating export jobs"""
    class Meta:
        model = ExportJob
        fields = [
            'dataset_type', 'fields', 'filters',
            'start_date', 'end_date', 'export_format'
        ]


class ScheduledExportSerializer(serializers.ModelSerializer):
    """Serializer for ScheduledExport"""
    dataset_type_display = serializers.CharField(source='get_dataset_type_display', read_only=True)
    frequency_display = serializers.CharField(source='get_frequency_display', read_only=True)
    export_format_display = serializers.CharField(source='get_export_format_display', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    recipient_emails = serializers.SerializerMethodField()
    
    class Meta:
        model = ScheduledExport
        fields = [
            'id', 'chama', 'name', 'description',
            'dataset_type', 'dataset_type_display', 'fields', 'filters',
            'date_range_type', 'frequency', 'frequency_display',
            'day_of_week', 'day_of_month', 'export_format', 'export_format_display',
            'recipients', 'recipient_emails',
            'is_active', 'last_run_at', 'next_run_at',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at', 'last_run_at', 'next_run_at']
    
    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name()
        return None
    
    def get_recipient_emails(self, obj):
        return obj.recipients


class ScheduledExportCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating scheduled exports"""
    class Meta:
        model = ScheduledExport
        fields = [
            'name', 'description', 'dataset_type', 'fields', 'filters',
            'date_range_type', 'frequency', 'day_of_week', 'day_of_month',
            'export_format', 'recipients'
        ]


class AvailableFieldsSerializer(serializers.Serializer):
    """Serializer for available export fields by dataset"""
    dataset_type = serializers.CharField()
    dataset_label = serializers.CharField()
    fields = ExportFieldSerializer(many=True)


class ExportRequestSerializer(serializers.Serializer):
    """Serializer for export request"""
    dataset_type = serializers.ChoiceField(choices=ExportJob.DatasetType.choices)
    fields = serializers.ListField(child=serializers.CharField(), required=False)
    filters = serializers.DictField(required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    export_format = serializers.ChoiceField(choices=ExportJob.ExportFormat.choices)


class ScheduleExportRequestSerializer(serializers.Serializer):
    """Serializer for scheduling an export"""
    name = serializers.CharField()
    description = serializers.CharField(required=False)
    dataset_type = serializers.ChoiceField(choices=ExportJob.DatasetType.choices)
    fields = serializers.ListField(child=serializers.CharField(), required=False)
    filters = serializers.DictField(required=False)
    date_range_type = serializers.CharField()
    frequency = serializers.ChoiceField(choices=ScheduledExport.ScheduleFrequency.choices)
    day_of_week = serializers.IntegerField(required=False)
    day_of_month = serializers.IntegerField(required=False)
    export_format = serializers.ChoiceField(choices=ExportJob.ExportFormat.choices)
    recipients = serializers.ListField(child=serializers.EmailField())


class ExportStatsSerializer(serializers.Serializer):
    """Serializer for export statistics"""
    total_exports = serializers.IntegerField()
    completed_exports = serializers.IntegerField()
    failed_exports = serializers.IntegerField()
    scheduled_exports = serializers.IntegerField()
    total_data_exported_mb = serializers.DecimalField(max_digits=14, decimal_places=2)
    recent_exports = ExportJobSerializer(many=True)
