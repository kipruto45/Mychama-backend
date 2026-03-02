# Exports Module URL Configuration

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ExportJobViewSet, ScheduledExportViewSet, ExportFieldViewSet,
    CreateExportView, ExportStatsView, DownloadExportView, CancelExportView,
    GetAvailableDatasetsView
)

router = DefaultRouter()
router.register(r'jobs', ExportJobViewSet, basename='export-job')
router.register(r'schedules', ScheduledExportViewSet, basename='scheduled-export')
router.register(r'fields', ExportFieldViewSet, basename='export-field')

urlpatterns = [
    path('create/', CreateExportView.as_view(), name='create-export'),
    path('stats/', ExportStatsView.as_view(), name='export-stats'),
    path('download/<uuid:job_id>/', DownloadExportView.as_view(), name='download-export'),
    path('cancel/<uuid:job_id>/', CancelExportView.as_view(), name='cancel-export'),
    path('datasets/', GetAvailableDatasetsView.as_view(), name='available-datasets'),
    path('', include(router.urls)),
]
