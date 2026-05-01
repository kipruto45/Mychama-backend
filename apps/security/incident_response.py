"""
Incident Response Models and Services

Implements incident management, escalation, and response workflows.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.accounts.models import User


class IncidentSeverity(models.IntegerChoices):
    """Incident severity levels."""

    P1_CRITICAL = 1
    P2_HIGH = 2
    P3_MEDIUM = 3
    P4_LOW = 4


class IncidentStatus(models.TextChoices):
    """Incident status."""

    OPEN = "open", "Open"
    INVESTIGATING = "investigating", "Investigating"
    CONTAINED = "contained", "Contained"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"
    REOPENED = "reopened", "Reopened"


class IncidentCategory(models.TextChoices):
    """Incident categories."""

    SECURITY_BREACH = "security_breach", "Security Breach"
    AUTH_BYPASS = "auth_bypass", "Authentication Bypass"
    PAYMENT_FRAUD = "payment_fraud", "Payment Fraud"
    DATA_LEAK = "data_leak", "Data Leak"
    SERVICE_OUTAGE = "service_outage", "Service Outage"
    PAYMENT_GATEWAY = "payment_gateway", "Payment Gateway Failure"
    KYC_FAILURE = "kyc_failure", "KYC Provider Failure"
    FRAUD_SPIKE = "fraud_spike", "Fraud Spike"
    SYSTEM_COMPROMISE = "system_compromise", "System Compromise"
    COMPLIANCE = "compliance", "Compliance Issue"
    OTHER = "other", "Other"


class Incident(models.Model):
    """Incident tracking model."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    incident_id = models.CharField(max_length=32, unique=True, db_index=True)
    title = models.CharField(max_length=255)
    description = models.TextField()
    severity = models.PositiveSmallIntegerField(
        choices=IncidentSeverity.choices,
        default=IncidentSeverity.P3_MEDIUM,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=IncidentStatus.choices,
        default=IncidentStatus.OPEN,
        db_index=True,
    )
    category = models.CharField(
        max_length=32,
        choices=IncidentCategory.choices,
    )
    triggered_by = models.ForeignKey(
        "security.SecurityAlert",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_incidents",
    )
    affected_users = models.PositiveIntegerField(default=0)
    financial_impact = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    data_breach = models.BooleanField(default=False)
    escalation_level = models.PositiveSmallIntegerField(default=0)
    resolution_summary = models.TextField(blank=True)
    root_cause = models.TextField(blank=True)
    lessons_learned = models.TextField(blank=True)

    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reported_incidents",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_incidents",
    )

    sla_deadline = models.DateTimeField(null=True, blank=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-severity", "-created_at"]
        indexes = [
            models.Index(fields=["severity", "status"]),
            models.Index(fields=["status", "sla_deadline"]),
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["severity", "created_at"]),
        ]

    def __str__(self):
        return f"INC-{self.incident_id}: {self.title}"

    @property
    def is_sla_breached(self) -> bool:
        if not self.sla_deadline:
            return False
        return timezone.now() > self.sla_deadline and self.status not in [
            IncidentStatus.RESOLVED,
            IncidentStatus.CLOSED,
        ]

    @property
    def severity_name(self) -> str:
        return IncidentSeverity(self.severity).name

    @classmethod
    def generate_incident_id(cls) -> str:
        today = timezone.now().strftime("%Y%m%d")
        count = cls.objects.filter(created_at__date=timezone.now().date()).count() + 1
        return f"{today}-{count:04d}"


class IncidentTimeline(models.Model):
    """Timeline events for incidents."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    incident = models.ForeignKey(
        Incident,
        on_delete=models.CASCADE,
        related_name="timeline",
    )
    event_type = models.CharField(max_length=50)
    description = models.TextField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incident_timeline_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.incident.incident_id}:{self.event_type}"


class IncidentCommunication(models.Model):
    """Communications about incidents."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    incident = models.ForeignKey(
        Incident,
        on_delete=models.CASCADE,
        related_name="communications",
    )
    recipient_type = models.CharField(
        max_length=20,
        choices=[
            ("users", "Affected Users"),
            ("staff", "Internal Staff"),
            ("board", "Board/Management"),
            ("regulator", "Regulators"),
            ("public", "Public"),
        ],
    )
    subject = models.CharField(max_length=255)
    message = models.TextField()
    sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.incident.incident_id}:{self.recipient_type}"


class IncidentEscalation(models.Model):
    """Tracks incident escalation levels."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    incident = models.ForeignKey(
        Incident,
        on_delete=models.CASCADE,
        related_name="escalations",
    )
    from_level = models.PositiveSmallIntegerField()
    to_level = models.PositiveSmallIntegerField()
    reason = models.TextField()
    escalated_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="escalated_incidents",
    )
    escalated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="escalation_actions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.incident.incident_id}:{self.from_level}->{self.to_level}"


class Runbook(models.Model):
    """Incident response runbooks."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    category = models.CharField(
        max_length=32,
        choices=IncidentCategory.choices,
    )
    severity = models.PositiveSmallIntegerField(
        choices=IncidentSeverity.choices,
    )
    trigger_conditions = models.TextField()
    diagnosis_steps = models.TextField()
    containment_steps = models.TextField()
    recovery_steps = models.TextField()
    communication_template = models.TextField()
    evidence_collection = models.TextField()
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-severity", "title"]

    def __str__(self):
        return f"{self.title} v{self.version}"


class IncidentResponseService:
    """Service for incident management."""

    SLA_CONFIG = {
        IncidentSeverity.P1_CRITICAL: timedelta(minutes=60),
        IncidentSeverity.P2_HIGH: timedelta(hours=4),
        IncidentSeverity.P3_MEDIUM: timedelta(hours=24),
        IncidentSeverity.P4_LOW: timedelta(hours=72),
    }

    @staticmethod
    def create_incident(
        title: str,
        description: str,
        severity: int,
        category: str,
        triggered_by=None,
        reported_by=None,
    ) -> Incident:
        """Create a new incident."""
        incident = Incident.objects.create(
            incident_id=Incident.generate_incident_id(),
            title=title,
            description=description,
            severity=severity,
            category=category,
            triggered_by=triggered_by,
            reported_by=reported_by,
            sla_deadline=timezone.now() + IncidentResponseService.SLA_CONFIG[severity],
        )

        IncidentTimeline.objects.create(
            incident=incident,
            event_type="created",
            description=f"Incident created with severity {IncidentSeverity(severity).name}",
            actor=reported_by,
        )

        return incident

    @staticmethod
    def assign_incident(incident: Incident, assignee: User, assigner: User) -> Incident:
        """Assign incident to a user."""
        incident.assigned_to = assignee
        incident.save(update_fields=["assigned_to", "updated_at"])

        IncidentTimeline.objects.create(
            incident=incident,
            event_type="assigned",
            description=f"Assigned to {assignee.full_name}",
            actor=assigner,
        )

        return incident

    @staticmethod
    def escalate_incident(
        incident: Incident,
        to_level: int,
        reason: str,
        escalated_to: User,
        escalator: User,
    ) -> Incident:
        """Escalate incident."""
        old_level = incident.escalation_level
        incident.escalation_level = to_level
        incident.severity = min(incident.severity, to_level)
        incident.save()

        IncidentEscalation.objects.create(
            incident=incident,
            from_level=old_level,
            to_level=to_level,
            reason=reason,
            escalated_to=escalated_to,
            escalated_by=escalator,
        )

        IncidentTimeline.objects.create(
            incident=incident,
            event_type="escalated",
            description=f"Escalated to level {to_level}: {reason}",
            actor=escalator,
        )

        return incident

    @staticmethod
    def resolve_incident(
        incident: Incident,
        resolution: str,
        root_cause: str = "",
        lessons: str = "",
        resolver: User | None = None,
    ) -> Incident:
        """Resolve incident."""
        incident.status = IncidentStatus.RESOLVED
        incident.resolution_summary = resolution
        incident.root_cause = root_cause
        incident.lessons_learned = lessons
        incident.resolved_at = timezone.now()
        incident.save()

        IncidentTimeline.objects.create(
            incident=incident,
            event_type="resolved",
            description=f"Resolved: {resolution}",
            actor=resolver,
        )

        return incident

    @staticmethod
    def get_escalation_contacts(level: int) -> list:
        """Get escalation contacts for a level."""
        contacts = {
            1: ["security@mychama.com", "+254700000001"],
            2: ["ops@mychama.com", "+254700000002"],
            3: ["support@mychama.com"],
        }
        return contacts.get(level, [])


class BreachNotificationService:
    """Service for data breach notification."""

    @staticmethod
    def assess_breach(incident: Incident) -> dict:
        """Assess if incident constitutes a data breach."""
        if not incident.data_breach:
            return {"is_breach": False}

        affected_count = incident.affected_users

        return {
            "is_breach": True,
            "requires_notification": affected_count > 0,
            "regulator_notification_required": affected_count > 500,
            "notification_deadline": incident.created_at + timedelta(hours=72),
            "estimated_notification_count": affected_count,
        }

    @staticmethod
    def prepare_member_notification(incident: Incident) -> dict:
        """Prepare member notification content."""
        return {
            "subject": "Important: Security Incident Notification",
            "template": "emails/security_incident.html",
            "variables": {
                "incident_date": incident.created_at.strftime("%Y-%m-%d"),
                "incident_description": incident.description[:200],
                "actions_taken": incident.resolution_summary,
                "support_contact": "support@mychama.com",
            },
        }


__all__ = [
    "Incident",
    "IncidentTimeline",
    "IncidentCommunication",
    "IncidentEscalation",
    "Runbook",
    "IncidentResponseService",
    "BreachNotificationService",
    "IncidentSeverity",
    "IncidentStatus",
    "IncidentCategory",
]
