# Exports Module Models
# Handles data exports, scheduled exports, and download management

from django.db import models

from apps.accounts.models import User
from apps.chama.models import Chama


class DatasetType(models.TextChoices):
    MEMBERS = 'MEMBERS', 'Members'
    CONTRIBUTIONS = 'CONTRIBUTIONS', 'Contributions'
    FINES = 'FINES', 'Fines'
    LOANS = 'LOANS', 'Loans'
    EXPENSES = 'EXPENSES', 'Expenses'
    INVESTMENTS = 'INVESTMENTS', 'Investments'
    MEETINGS = 'MEETINGS', 'Meetings'
    AUDIT_LOGS = 'AUDIT_LOGS', 'Audit Logs'
    PAYMENTS = 'PAYMENTS', 'Payments'
    ALL_FINANCIAL = 'ALL_FINANCIAL', 'All Financial Data'
    COMPLIANCE_PACK = 'COMPLIANCE_PACK', 'Compliance Pack'


class ExportFormat(models.TextChoices):
    CSV = 'CSV', 'CSV'
    XLSX = 'XLSX', 'Excel'
    PDF = 'PDF', 'PDF'


class ExportStatus(models.TextChoices):
    QUEUED = 'QUEUED', 'Queued'
    RUNNING = 'RUNNING', 'Running'
    READY = 'READY', 'Ready'
    FAILED = 'FAILED', 'Failed'
    EXPIRED = 'EXPIRED', 'Expired'


class ScheduleFrequency(models.TextChoices):
    DAILY = 'DAILY', 'Daily'
    WEEKLY = 'WEEKLY', 'Weekly'
    MONTHLY = 'MONTHLY', 'Monthly'


class ExportJob(models.Model):
    """Represents an export job request"""
    chama = models.ForeignKey(Chama, on_delete=models.CASCADE, related_name='export_jobs')
    
    # What to export
    dataset_type = models.CharField(max_length=50, choices=DatasetType.choices)
    fields = models.JSONField(default=list, blank=True)  # List of field names to include
    filters = models.JSONField(default=dict, blank=True)  # Filter criteria
    
    # Date range
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    
    # Format
    export_format = models.CharField(max_length=10, choices=ExportFormat.choices)
    
    # Status
    status = models.CharField(max_length=20, choices=ExportStatus.choices, default=ExportStatus.QUEUED)
    
    # Output
    file_url = models.URLField(blank=True)
    file_size = models.PositiveIntegerField(null=True, blank=True)  # in bytes
    record_count = models.PositiveIntegerField(default=0)
    
    # Error info
    error_message = models.TextField(blank=True)
    
    # Expiry
    expires_at = models.DateTimeField(null=True, blank=True)
    
    # Requested by
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='export_jobs')
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Export {self.dataset_type} - {self.get_export_format_display()} (#{self.id})"


class ScheduledExport(models.Model):
    """Represents a recurring export schedule"""
    chama = models.ForeignKey(Chama, on_delete=models.CASCADE, related_name='scheduled_exports')
    
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    
    # What to export
    dataset_type = models.CharField(max_length=50, choices=DatasetType.choices)
    fields = models.JSONField(default=list, blank=True)
    filters = models.JSONField(default=dict, blank=True)
    
    # Date range type (relative to now)
    date_range_type = models.CharField(max_length=20, default='LAST_30_DAYS')  # LAST_7_DAYS, LAST_30_DAYS, LAST_90_DAYS, THIS_MONTH, THIS_YEAR, CUSTOM
    
    # Schedule
    frequency = models.CharField(max_length=20, choices=ScheduleFrequency.choices)
    day_of_week = models.PositiveIntegerField(null=True, blank=True)  # 0=Monday, 6=Sunday
    day_of_month = models.PositiveIntegerField(null=True, blank=True)  # 1-31
    
    # Format
    export_format = models.CharField(max_length=10, choices=ExportFormat.choices, default=ExportFormat.CSV)
    
    # Recipients
    recipients = models.JSONField(default=list)  # List of email addresses
    
    # Status
    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    
    # Created by
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='scheduled_exports')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
    
    def __str__(self):
        return f"Scheduled Export: {self.name} ({self.get_frequency_display()})"


class ExportField(models.Model):
    """Available fields for each dataset type"""
    dataset_type = models.CharField(max_length=50, choices=DatasetType.choices)
    field_name = models.CharField(max_length=100)
    field_label = models.CharField(max_length=255)
    field_type = models.CharField(max_length=50)  # string, number, date, boolean, etc.
    field_order = models.PositiveIntegerField(default=0)  # For ordering fields in export UI
    is_sensitive = models.BooleanField(default=False)  # e.g., phone, email, ID numbers
    is_default = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    
    class Meta:
        unique_together = ['dataset_type', 'field_name']
        ordering = ['dataset_type', 'field_order']
    
    def __str__(self):
        return f"{self.dataset_type}.{self.field_name}"


class ExportPermission(models.Model):
    """Role-based export permissions"""
    chama = models.ForeignKey(Chama, on_delete=models.CASCADE, related_name='export_permissions')
    
    role = models.CharField(max_length=50)  # treasurer, secretary, member, etc.
    allowed_datasets = models.JSONField(default=list)  # List of DatasetType values
    can_schedule = models.BooleanField(default=False)
    can_view_all = models.BooleanField(default=False)  # Can see all members' data
    
    class Meta:
        unique_together = ['chama', 'role']
    
    def __str__(self):
        return f"Export permissions for {self.role} in {self.chama.name}"
