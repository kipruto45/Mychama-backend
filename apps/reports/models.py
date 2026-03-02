from django.conf import settings
from django.db import models

from core.models import BaseModel


class ReportType(models.TextChoices):
    # Member Reports
    MEMBER_STATEMENT = "member_statement", "Member Statement"
    MEMBER_CONTRIBUTIONS = "member_contributions", "Member Contributions"
    MEMBER_LOANS = "member_loans", "Member Loans"
    MEMBER_WITHDRAWALS = "member_withdrawals", "Member Withdrawals"
    MEMBER_RECEIPTS = "member_receipts", "Member Receipts"
    
    # Admin/Chama Reports
    CHAMA_SUMMARY = "chama_summary", "Chama Fund Summary"
    CHAMA_LEDGER = "chama_ledger", "Ledger Report"
    CHAMA_CONTRIBUTIONS = "chama_contributions", "Contributions Report"
    CHAMA_LOANS = "chama_loans", "Loans Report"
    CHAMA_PAYOUTS = "chama_payouts", "Payouts Report"
    CHAMA_ARREARS = "chama_arrears", "Arrears/Delinquency"
    CHAMA_AUDIT = "chama_audit", "Audit Report"
    CHAMA_RECONCILIATION = "chama_reconciliation", "Reconciliation Report"
    
    # Analytics
    LOAN_STATEMENT = "loan_statement", "Loan Statement"
    LOAN_MONTHLY_SUMMARY = "loan_monthly_summary", "Loan Monthly Summary"
    LOAN_SCHEDULE = "loan_schedule", "Loan Repayment Schedule"
    LOAN_APPROVALS_LOG = "loan_approvals_log", "Loan Approvals Log"
    CHAMA_HEALTH_SCORE = "chama_health_score", "Chama Health Score"
    COLLECTION_FORECAST = "collection_forecast", "Collection Forecast"
    DEFAULTER_RISK = "defaulter_risk", "Defaulter Risk"
    COHORT_ANALYSIS = "cohort_analysis", "Cohort Analysis"


class ReportFormat(models.TextChoices):
    JSON = "json", "JSON"
    PDF = "pdf", "PDF"
    XLSX = "xlsx", "Excel"
    CSV = "csv", "CSV"


class ReportStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class ReportScope(models.TextChoices):
    MEMBER = "member", "Member"
    CHAMA = "chama", "Chama"
    SYSTEM = "system", "System"


class ReportRunStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class ReportRequest(BaseModel):
    """
    Generate/export report jobs.
    One source of truth for all report generation.
    """
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="report_requests",
    )
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="report_requests",
        null=True,
        blank=True,
    )
    
    # Report definition
    scope = models.CharField(max_length=20, choices=ReportScope.choices)
    report_type = models.CharField(max_length=50, choices=ReportType.choices)
    
    # Filters (JSON)
    filters = models.JSONField(default=dict, blank=True)
    # {
    #   "date_from": "2024-01-01",
    #   "date_to": "2024-12-31",
    #   "member_id": 123,
    #   "status": "active",
    # }
    
    # Output
    format = models.CharField(max_length=10, choices=ReportFormat.choices)
    status = models.CharField(
        max_length=20,
        choices=ReportStatus.choices,
        default=ReportStatus.QUEUED,
    )
    file_path = models.CharField(max_length=500, blank=True)
    file_name = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveIntegerField(null=True, blank=True)
    
    # Error handling
    error_message = models.TextField(blank=True)
    
    # Timestamps
    completed_at = models.DateTimeField(null=True, blank=True)
    
    # For member download tracking
    is_member_download = models.BooleanField(default=False)
    
    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["requested_by", "status"]),
            models.Index(fields=["chama", "status"]),
            models.Index(fields=["report_type", "status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.report_type} - {self.format} ({self.status})"


class ScheduledReport(BaseModel):
    """
    Automated scheduled reports.
    """
    name = models.CharField(max_length=100)
    
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="scheduled_reports",
    )
    
    # Report definition
    report_type = models.CharField(max_length=50, choices=ReportType.choices)
    scope = models.CharField(max_length=20, choices=ReportScope.choices)
    filters = models.JSONField(default=dict, blank=True)
    format = models.CharField(max_length=10, choices=ReportFormat.choices)
    
    # Schedule (cron string)
    # Examples:
    # "0 9 * * 1" - Every Monday at 9 AM
    # "0 0 1 * *" - First day of month
    # "0 9 * * *" - Daily at 9 AM
    schedule = models.CharField(max_length=100)
    
    # Recipients (comma-separated user IDs or role names)
    recipients = models.JSONField(default=list)
    # ["user_1", "user_2", "treasurer_role"]
    
    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=20, blank=True)
    
    # Next run calculation
    next_run_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["chama", "is_active"]),
            models.Index(fields=["schedule", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.schedule})"


class ReportRun(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama", on_delete=models.CASCADE, related_name="report_runs"
    )
    report_type = models.CharField(max_length=50, choices=ReportType.choices)
    format = models.CharField(
        max_length=10,
        choices=ReportFormat.choices,
        default=ReportFormat.JSON,
    )
    status = models.CharField(
        max_length=20,
        choices=ReportRunStatus.choices,
        default=ReportRunStatus.PENDING,
    )
    parameters = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_reports",
    )
    is_async = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["chama", "report_type", "created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.report_type} ({self.format}) - {self.status}"


class StatementDownloadHistory(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="statement_downloads",
    )
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="statement_downloads",
    )
    report_type = models.CharField(max_length=50, choices=ReportType.choices)
    format = models.CharField(max_length=10, choices=ReportFormat.choices)
    file_name = models.CharField(max_length=255)
    period_month = models.PositiveSmallIntegerField(null=True, blank=True)
    period_year = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "chama", "created_at"]),
            models.Index(fields=["report_type", "format", "created_at"]),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.report_type}:{self.format}"
