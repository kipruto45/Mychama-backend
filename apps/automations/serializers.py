from __future__ import annotations

from rest_framework import serializers

from apps.automations.models import (
    AutomationRule,
    JobRun,
    NotificationLog,
    ScheduledJob,
)


class ScheduledJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduledJob
        fields = [
            "id",
            "name",
            "description",
            "is_enabled",
            "schedule",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class JobRunSerializer(serializers.ModelSerializer):
    job_name = serializers.CharField(source="job.name", read_only=True)

    class Meta:
        model = JobRun
        fields = [
            "id",
            "job",
            "job_name",
            "started_at",
            "finished_at",
            "status",
            "meta",
            "error",
            "created_at",
        ]


class AutomationNotificationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationLog
        fields = [
            "id",
            "user",
            "chama",
            "channel",
            "message",
            "status",
            "provider_response",
            "created_at",
        ]


class AutomationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutomationRule
        fields = [
            "id",
            "chama",
            "rule_type",
            "config",
            "is_enabled",
            "created_at",
            "updated_at",
        ]
