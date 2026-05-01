from __future__ import annotations

import uuid

from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.automations.catalog import get_automation_catalog
from apps.automations.models import JobRun, NotificationLog, ScheduledJob
from apps.automations.permissions import IsAutomationReader
from apps.automations.serializers import (
    AutomationNotificationLogSerializer,
    JobRunSerializer,
    ScheduledJobSerializer,
)
from apps.billing.gating import require_feature
from apps.payments.models import PaymentReconciliationRun


class AutomationScopeMixin:
    def get_scoped_chama_id(self):
        if getattr(self, "swagger_fake_view", False):
            return None
        candidates = [
            self.kwargs.get("chama_id"),
            self.request.headers.get("X-CHAMA-ID"),
            self.request.query_params.get("chama_id"),
        ]
        resolved = None
        for raw in candidates:
            if raw in [None, ""]:
                continue
            try:
                parsed = str(uuid.UUID(str(raw)))
            except (TypeError, ValueError) as exc:
                raise ValidationError({"chama_id": "Invalid chama_id."}) from exc
            if resolved and parsed != resolved:
                raise ValidationError({"chama_id": "Conflicting chama_id values."})
            resolved = parsed
        return resolved


class ScheduledJobListView(AutomationScopeMixin, generics.ListAPIView):
    serializer_class = ScheduledJobSerializer
    permission_classes = [permissions.IsAuthenticated, IsAutomationReader]

    @require_feature('automations_read')
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ScheduledJob.objects.none()
        return ScheduledJob.objects.order_by("name")


class JobRunListView(AutomationScopeMixin, generics.ListAPIView):
    serializer_class = JobRunSerializer
    permission_classes = [permissions.IsAuthenticated, IsAutomationReader]

    @require_feature('automations_read')
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return JobRun.objects.none()
        job_name = self.request.query_params.get("job")
        queryset = JobRun.objects.select_related("job").order_by("-started_at")
        if job_name:
            queryset = queryset.filter(job__name=job_name)
        return queryset


class JobDetailRunsView(AutomationScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsAutomationReader]

    @require_feature('automations_read')
    def get(self, request, name):
        job = get_object_or_404(ScheduledJob, name=name)
        runs = JobRun.objects.filter(job=job).order_by("-started_at")[:100]
        return Response(
            {
                "job": ScheduledJobSerializer(job).data,
                "runs": JobRunSerializer(runs, many=True).data,
            }
        )


class AutomationNotificationLogListView(AutomationScopeMixin, generics.ListAPIView):
    serializer_class = AutomationNotificationLogSerializer
    permission_classes = [permissions.IsAuthenticated, IsAutomationReader]

    @require_feature('automations_read')
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return NotificationLog.objects.none()
        chama_id = self.get_scoped_chama_id()
        if not chama_id:
            raise ValidationError({"chama_id": "chama_id is required."})
        queryset = NotificationLog.objects.filter(chama_id=chama_id).order_by("-created_at")
        status_value = self.request.query_params.get("status")
        channel = self.request.query_params.get("channel")
        if status_value:
            queryset = queryset.filter(status=status_value)
        if channel:
            queryset = queryset.filter(channel=channel)
        return queryset


class ReconciliationReportView(AutomationScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsAutomationReader]

    @require_feature('reconciliation_dashboard')
    def get(self, request):
        chama_id = self.get_scoped_chama_id()
        if not chama_id:
            raise ValidationError({"chama_id": "chama_id is required."})

        runs = PaymentReconciliationRun.objects.filter(chama_id=chama_id).order_by("-run_at")[:30]
        payload = [
            {
                "id": str(run.id),
                "run_at": run.run_at.isoformat(),
                "status": run.status,
                "totals": run.totals,
                "anomalies": run.anomalies,
            }
            for run in runs
        ]
        return Response({"count": len(payload), "runs": payload})


class AutomationCatalogView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        return Response(get_automation_catalog())
