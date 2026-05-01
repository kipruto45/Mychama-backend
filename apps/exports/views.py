# Exports Module Views
# API endpoints for data exports and downloads

from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.billing.gating import BillingAccessMixin

from .models import ExportField, ExportJob, ScheduledExport
from .serializers import (
    ExportFieldSerializer,
    ExportJobCreateSerializer,
    ExportJobSerializer,
    ExportRequestSerializer,
    ExportStatsSerializer,
    ScheduledExportCreateSerializer,
    ScheduledExportSerializer,
)


class ExportJobViewSet(BillingAccessMixin, viewsets.ModelViewSet):
    """ViewSet for managing export jobs"""
    serializer_class = ExportJobSerializer
    permission_classes = [IsAuthenticated]
    billing_feature_key = 'exports_pdf'
    billing_or_features = ['exports_excel']
    
    def get_queryset(self):
        user = self.request.user
        chama_id = self.request.query_params.get('chama_id')
        status_filter = self.request.query_params.get('status')
        
        queryset = ExportJob.objects.select_related('requested_by', 'chama').all()
        
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter to user's exports
        queryset = queryset.filter(requested_by=user)
        
        return queryset
    
    def get_serializer_class(self):
        if self.action == 'create':
            return ExportJobCreateSerializer
        return ExportJobSerializer
    
    def perform_create(self, serializer):
        export = serializer.save(
            requested_by=self.request.user,
            status='QUEUED'
        )
        
        # In production, this would queue the export job
        # For now, we'll just save it
        return export
    
    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        """Get download URL for completed export"""
        export = self.get_object()
        
        if export.status != 'READY':
            return Response(
                {'error': 'Export not ready'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if export.expires_at and export.expires_at < timezone.now():
            return Response(
                {'error': 'Download link expired'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        return Response({
            'download_url': export.file_url,
            'expires_at': export.expires_at
        })
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Get export statistics"""
        chama_id = request.query_params.get('chama_id')
        
        if not chama_id:
            return Response(
                {'error': 'chama_id required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        exports = ExportJob.objects.filter(chama_id=chama_id)
        
        total = exports.count()
        completed = exports.filter(status='READY').count()
        failed = exports.filter(status='FAILED').count()
        scheduled = ScheduledExport.objects.filter(chama_id=chama_id, is_active=True).count()
        
        total_size = exports.filter(file_size__isnull=False).aggregate(
            total=Sum('file_size')
        )['total'] or 0
        
        recent = exports.order_by('-created_at')[:5]
        
        data = {
            'total_exports': total,
            'completed_exports': completed,
            'failed_exports': failed,
            'scheduled_exports': scheduled,
            'total_data_exported_mb': total_size / (1024 * 1024),
            'recent_exports': ExportJobSerializer(recent, many=True).data
        }
        
        serializer = ExportStatsSerializer(data)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def create_export(self, request):
        """Create a new export job"""
        serializer = ExportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        chama_id = request.query_params.get('chama_id')
        
        if not chama_id:
            return Response(
                {'error': 'chama_id required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        data = serializer.validated_data
        
        export = ExportJob.objects.create(
            chama_id=chama_id,
            dataset_type=data['dataset_type'],
            fields=data.get('fields', []),
            filters=data.get('filters', {}),
            start_date=data.get('start_date'),
            end_date=data.get('end_date'),
            export_format=data.get('export_format', 'CSV'),
            requested_by=request.user,
            status='QUEUED',
            expires_at=timezone.now() + timedelta(days=7)
        )
        
        return Response(
            ExportJobSerializer(export).data,
            status=status.HTTP_201_CREATED
        )


class ScheduledExportViewSet(BillingAccessMixin, viewsets.ModelViewSet):
    """ViewSet for managing scheduled exports"""
    serializer_class = ScheduledExportSerializer
    permission_classes = [IsAuthenticated]
    billing_feature_key = 'exports_pdf'
    billing_or_features = ['exports_excel']
    
    def get_queryset(self):
        chama_id = self.request.query_params.get('chama_id')
        
        queryset = ScheduledExport.objects.select_related('chama', 'created_by').all()
        
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        
        return queryset
    
    def get_serializer_class(self):
        if self.action == 'create' or self.action == 'update':
            return ScheduledExportCreateSerializer
        return ScheduledExportSerializer
    
    def perform_create(self, serializer):
        return serializer.save(created_by=self.request.user)
    
    @action(detail=True, methods=['post'])
    def toggle(self, request, pk=None):
        """Toggle scheduled export active status"""
        scheduled = self.get_object()
        scheduled.is_active = not scheduled.is_active
        scheduled.save()
        
        return Response({
            'status': 'success',
            'is_active': scheduled.is_active
        })
    
    @action(detail=True, methods=['post'])
    def run_now(self, request, pk=None):
        """Trigger an immediate export"""
        scheduled = self.get_object()
        
        # Create a new export job
        export = ExportJob.objects.create(
            chama=scheduled.chama,
            dataset_type=scheduled.dataset_type,
            fields=scheduled.fields,
            filters=scheduled.filters,
            export_format=scheduled.export_format,
            requested_by=request.user,
            status='QUEUED',
            expires_at=timezone.now() + timedelta(days=7)
        )
        
        # Update last run time
        scheduled.last_run_at = timezone.now()
        scheduled.save()
        
        return Response({
            'status': 'success',
            'export_id': export.id
        })
    
    @action(detail=False, methods=['get'])
    def available_fields(self, request):
        """Get available fields for each dataset type"""
        fields_by_dataset = {}
        
        for dataset_type in ExportJob.DatasetType:
            fields = ExportField.objects.filter(dataset_type=dataset_type)
            fields_by_dataset[dataset_type] = {
                'label': dataset_type.label,
                'fields': list(fields.values('field_name', 'field_label', 'field_type', 'is_sensitive', 'is_default'))
            }
        
        return Response(fields_by_dataset)


class ExportFieldViewSet(BillingAccessMixin, viewsets.ReadOnlyModelViewSet):
    """ViewSet for viewing available export fields"""
    serializer_class = ExportFieldSerializer
    permission_classes = [IsAuthenticated]
    billing_feature_key = 'exports_pdf'
    billing_or_features = ['exports_excel']
    
    def get_queryset(self):
        dataset_type = self.request.query_params.get('dataset_type')
        
        queryset = ExportField.objects.all()
        
        if dataset_type:
            queryset = queryset.filter(dataset_type=dataset_type)
        
        return queryset
