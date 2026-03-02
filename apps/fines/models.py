# Fines Module Models
# Handles fine rules, fine issuance, payments, and adjustments

from django.db import models
from django.conf import settings
from apps.chama.models import Chama
from apps.accounts.models import User


class FineCategory(models.TextChoices):
    LATE_CONTRIBUTION = 'LATE_CONTRIBUTION', 'Late Contribution'
    MISSED_MEETING = 'MISSED_MEETING', 'Missed Meeting'
    MISCONDUCT = 'MISCONDUCT', 'Misconduct'
    LOAN_PENALTY = 'LOAN_PENALTY', 'Loan Penalty'
    CUSTOM = 'CUSTOM', 'Custom'


class FineStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    DUE = 'DUE', 'Due'
    PAID = 'PAID', 'Paid'
    WAIVED = 'WAIVED', 'Waived'
    DISPUTED = 'DISPUTED', 'Disputed'
    OVERDUE = 'OVERDUE', 'Overdue'


class TriggerType(models.TextChoices):
    LATE_DUE = 'LATE_DUE', 'Late Due'
    MISSED_MEETING = 'MISSED_MEETING', 'Missed Meeting'
    LOAN_DEFAULT = 'LOAN_DEFAULT', 'Loan Default'
    MISCONDUCT = 'MISCONDUCT', 'Misconduct'
    CUSTOM = 'CUSTOM', 'Custom'


class AmountType(models.TextChoices):
    FLAT = 'FLAT', 'Flat Amount'
    PERCENT = 'PERCENT', 'Percentage'


class RecurrenceType(models.TextChoices):
    ONCE = 'ONCE', 'Once'
    DAILY = 'DAILY', 'Daily'
    WEEKLY = 'WEEKLY', 'Weekly'


class FineRule(models.Model):
    """Defines rules for automatic fine generation"""
    chama = models.ForeignKey(Chama, on_delete=models.CASCADE, related_name='fine_rules')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    
    # Trigger configuration
    trigger_type = models.CharField(max_length=50, choices=TriggerType.choices)
    category = models.CharField(max_length=50, choices=FineCategory.choices, default=FineCategory.CUSTOM)
    
    # Amount configuration
    amount_type = models.CharField(max_length=20, choices=AmountType.choices)
    amount_value = models.DecimalField(max_digits=12, decimal_places=2)
    
    # Additional settings
    grace_days = models.PositiveIntegerField(default=0)
    cap_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    recurrence = models.CharField(max_length=20, choices=RecurrenceType.choices, default=RecurrenceType.ONCE)
    
    # Status
    enabled = models.BooleanField(default=True)
    
    # Metadata
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_fine_rules')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.name} ({self.chama.name})"


class Fine(models.Model):
    """Individual fine issued to a member"""
    chama = models.ForeignKey(Chama, on_delete=models.CASCADE, related_name='fines')
    member = models.ForeignKey(User, on_delete=models.CASCADE, related_name='fines')
    category = models.CharField(max_length=50, choices=FineCategory.choices)
    
    # Source of fine (optional - may be from a rule or manually issued)
    rule = models.ForeignKey(FineRule, on_delete=models.SET_NULL, null=True, blank=True, related_name='issued_fines')
    
    # Fine details
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    due_date = models.DateField()
    status = models.CharField(max_length=20, choices=FineStatus.choices, default=FineStatus.PENDING)
    
    # Issue details
    issued_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='issued_fines')
    issued_reason = models.TextField()
    issued_at = models.DateTimeField(auto_now_add=True)
    
    # Attachments
    attachments = models.JSONField(default=list, blank=True)  # List of file URLs
    
    # Dispute
    disputed_at = models.DateTimeField(null=True, blank=True)
    dispute_reason = models.TextField(blank=True)
    dispute_resolved_at = models.DateTimeField(null=True, blank=True)
    dispute_resolution = models.TextField(blank=True)
    
    # Timestamps
    paid_at = models.DateTimeField(null=True, blank=True)
    waived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Fine #{self.id} - {self.member.get_full_name()} - {self.amount}"
    
    @property
    def outstanding_amount(self):
        """Calculate outstanding amount after payments and adjustments"""
        from django.db.models import Sum
        payments = FinePayment.objects.filter(fine=self).aggregate(total=Sum('amount'))['total'] or 0
        adjustments = FineAdjustment.objects.filter(fine=self).aggregate(total=Sum('before_amount') - Sum('after_amount'))['total'] or 0
        return max(0, float(self.amount) - float(payments))


class FineAdjustment(models.Model):
    """Tracks adjustments to fines (waivers, reductions, increases)"""
    fine = models.ForeignKey(Fine, on_delete=models.CASCADE, related_name='adjustments')
    
    # Before and after amounts
    before_amount = models.DecimalField(max_digits=12, decimal_places=2)
    after_amount = models.DecimalField(max_digits=12, decimal_places=2)
    
    # Reason for adjustment
    reason = models.TextField()
    
    # Who made the adjustment
    adjusted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='fine_adjustments')
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Adjustment on Fine #{self.fine.id}: {self.before_amount} -> {self.after_amount}"


class FinePayment(models.Model):
    """Tracks payments made towards fines"""
    class PaymentMethod(models.TextChoices):
        MPESA = 'MPESA', 'M-Pesa'
        BANK = 'BANK', 'Bank Transfer'
        CASH = 'CASH', 'Cash'
        OTHER = 'OTHER', 'Other'
    
    fine = models.ForeignKey(Fine, on_delete=models.CASCADE, related_name='payments')
    
    # Payment details
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    
    # Transaction reference
    transaction_reference = models.CharField(max_length=255, blank=True)
    
    # Recorded by
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='recorded_fine_payments')
    
    # Notes
    notes = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Payment of {self.amount} on Fine #{self.fine.id}"


class FineReminder(models.Model):
    """Tracks reminder notifications sent for fines"""
    fine = models.ForeignKey(Fine, on_delete=models.CASCADE, related_name='reminders')
    sent_to = models.ForeignKey(User, on_delete=models.CASCADE, related_name='fine_reminders')
    reminder_type = models.CharField(max_length=50)  # e.g., 'due_soon', 'overdue'
    sent_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, default='SENT')
    
    class Meta:
        ordering = ['-sent_at']
    
    def __str__(self):
        return f"Reminder for Fine #{self.fine.id} to {self.sent_to.get_full_name()}"
