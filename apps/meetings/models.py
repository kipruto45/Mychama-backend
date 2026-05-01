import secrets

from django.conf import settings
from django.db import models

from core.models import BaseModel


class AttendanceStatus(models.TextChoices):
    PRESENT = "present", "Present"
    ABSENT = "absent", "Absent"
    LATE = "late", "Late"
    EXCUSED = "excused", "Excused"


class ResolutionStatus(models.TextChoices):
    OPEN = "open", "Open"
    DONE = "done", "Done"


class AgendaItemStatus(models.TextChoices):
    PROPOSED = "proposed", "Proposed"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    DONE = "done", "Done"


class VoteChoice(models.TextChoices):
    YES = "yes", "Yes"
    NO = "no", "No"
    ABSTAIN = "abstain", "Abstain"


class LocationType(models.TextChoices):
    PHYSICAL = "physical", "Physical"
    ONLINE = "online", "Online"
    HYBRID = "hybrid", "Hybrid"


class MinutesStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING_APPROVAL = "pending_approval", "Pending Approval"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class Meeting(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="meetings",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    location_type = models.CharField(
        max_length=20,
        choices=LocationType.choices,
        default=LocationType.PHYSICAL,
    )
    meeting_link = models.URLField(blank=True)
    date = models.DateTimeField()
    agenda = models.TextField(blank=True)
    minutes_text = models.TextField(blank=True)
    minutes_file = models.FileField(upload_to="meeting_minutes/", null=True, blank=True)
    attendance_qr_token = models.CharField(max_length=64, blank=True, db_index=True)
    quorum_percentage = models.PositiveSmallIntegerField(default=50)
    minutes_status = models.CharField(
        max_length=20,
        choices=MinutesStatus.choices,
        default=MinutesStatus.DRAFT,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_meetings",
    )
    cancellation_reason = models.TextField(blank=True)
    minutes_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_meeting_minutes",
    )
    minutes_approved_at = models.DateTimeField(null=True, blank=True)

    def __init__(self, *args, **kwargs):
        # Backward compatibility for older payloads/tests.
        scheduled_at = kwargs.pop("scheduled_at", None)
        if scheduled_at is not None and "date" not in kwargs:
            kwargs["date"] = scheduled_at
        super().__init__(*args, **kwargs)

    class Meta:
        ordering = ["-date"]
        indexes = [
            models.Index(
                fields=["chama", "date"], name="meetings_me_chama_i_fb66b2_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.chama.name} - {self.title}"

    def save(self, *args, **kwargs):
        if not self.attendance_qr_token:
            self.attendance_qr_token = secrets.token_hex(24)
        super().save(*args, **kwargs)


class Attendance(BaseModel):
    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="attendance",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="meeting_attendance",
    )
    status = models.CharField(
        max_length=20,
        choices=AttendanceStatus.choices,
        default=AttendanceStatus.PRESENT,
    )
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["meeting", "member"],
                name="uniq_attendance_per_member_per_meeting",
            ),
        ]
        indexes = [
            models.Index(
                fields=["meeting", "status"], name="meetings_at_meeting_d03b95_idx"
            ),
            models.Index(
                fields=["member", "meeting"], name="meetings_at_member__aa38f7_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.member} @ {self.meeting} ({self.status})"


class Resolution(BaseModel):
    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="resolutions",
    )
    text = models.TextField()
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="meeting_resolutions",
    )
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=ResolutionStatus.choices,
        default=ResolutionStatus.OPEN,
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["due_date", "-created_at"]
        indexes = [
            models.Index(
                fields=["meeting", "status"], name="meetings_re_meeting_b72ca8_idx"
            ),
            models.Index(
                fields=["due_date", "status"], name="meetings_re_due_sta_0af3_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"Resolution {self.id} ({self.status})"


class AgendaItem(BaseModel):
    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="agenda_items",
    )
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="proposed_agenda_items",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    duration_minutes = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=AgendaItemStatus.choices,
        default=AgendaItemStatus.PROPOSED,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_agenda_items",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["meeting", "status"]),
            models.Index(fields=["proposed_by", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"


class MeetingVote(BaseModel):
    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="votes",
    )
    agenda_item = models.ForeignKey(
        AgendaItem,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="votes",
    )
    voter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="meeting_votes",
    )
    choice = models.CharField(max_length=10, choices=VoteChoice.choices)
    note = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["meeting", "agenda_item", "voter"],
                name="uniq_vote_per_meeting_item_voter",
            ),
        ]
        indexes = [
            models.Index(fields=["meeting", "agenda_item"]),
            models.Index(fields=["voter", "meeting"]),
        ]
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.meeting_id}:{self.voter_id}:{self.choice}"


class MinutesApproval(BaseModel):
    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="minutes_approvals",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="minutes_approval_reviews",
    )
    decision = models.CharField(max_length=20, choices=MinutesStatus.choices)
    note = models.TextField(blank=True)
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["meeting", "decision", "decided_at"]),
            models.Index(fields=["reviewer", "decided_at"]),
        ]
        ordering = ["-decided_at"]

    def __str__(self) -> str:
        return f"{self.meeting_id}:{self.decision}"
