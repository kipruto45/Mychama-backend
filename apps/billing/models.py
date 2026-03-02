"""
Billing Models
Plan, Subscription, SeatUsage, FeatureOverride
"""
import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone
from apps.chama.models import Chama


class Plan(models.Model):
    """
    Subscription Plan
    Defines plan tiers with entitlements matrix
    """
    # Plan codes
    FREE = 'FREE'
    PRO = 'PRO'
    ENTERPRISE = 'ENTERPRISE'
    
    PLAN_CHOICES = [
        (FREE, 'Free'),
        (PRO, 'Pro'),
        (ENTERPRISE, 'Enterprise'),
    ]
    
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    
    # Pricing
    monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    yearly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # Entitlements matrix (JSON)
    features = models.JSONField(default=dict, blank=True)
    
    # Status
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)
    stripe_monthly_price_id = models.CharField(max_length=100, blank=True, null=True)
    stripe_yearly_price_id = models.CharField(max_length=100, blank=True, null=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['sort_order']
        verbose_name = 'Plan'
        verbose_name_plural = 'Plans'
    
    def __str__(self):
        return f"{self.name} ({self.code})"
    
    def get_price(self, interval='monthly'):
        """Get price based on interval"""
        if interval == 'yearly':
            return self.yearly_price
        return self.monthly_price
    
    def get_feature(self, key, default=None):
        """Get feature value from entitlements matrix"""
        return self.features.get(key, default)


class Subscription(models.Model):
    """
    Chama Subscription
    Tracks subscription state for each Chama
    """
    # Status
    TRIALING = 'trialing'
    ACTIVE = 'active'
    PAUSED = 'paused'
    CANCELED = 'canceled'
    PAST_DUE = 'past_due'
    UNPAID = 'unpaid'
    
    STATUS_CHOICES = [
        (TRIALING, 'Trialing'),
        (ACTIVE, 'Active'),
        (PAUSED, 'Paused'),
        (CANCELED, 'Canceled'),
        (PAST_DUE, 'Past Due'),
        (UNPAID, 'Unpaid'),
    ]
    
    # Provider
    STRIPE = 'stripe'
    PAYPAL = 'paypal'
    MPESA = 'mpesa'
    MANUAL = 'manual'

    MONTHLY = 'monthly'
    YEARLY = 'yearly'

    BILLING_CYCLE_CHOICES = [
        (MONTHLY, 'Monthly'),
        (YEARLY, 'Yearly'),
    ]
    
    PROVIDER_CHOICES = [
        (STRIPE, 'Stripe'),
        (PAYPAL, 'PayPal'),
        (MPESA, 'M-Pesa'),
        (MANUAL, 'Manual'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    chama = models.ForeignKey(
        Chama,
        on_delete=models.CASCADE,
        related_name='subscriptions'
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
    )
    
    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=ACTIVE)
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default=MANUAL)
    
    # Provider IDs
    provider_subscription_id = models.CharField(max_length=100, blank=True, null=True)
    
    # Period
    billing_cycle = models.CharField(
        max_length=20,
        choices=BILLING_CYCLE_CHOICES,
        default=MONTHLY,
    )
    current_period_start = models.DateTimeField(blank=True, null=True)
    current_period_end = models.DateTimeField(blank=True, null=True)
    cancel_at_period_end = models.BooleanField(default=False)
    auto_renew = models.BooleanField(default=True)
    grace_period_ends_at = models.DateTimeField(blank=True, null=True)
    suspended_at = models.DateTimeField(blank=True, null=True)
    scheduled_plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='scheduled_subscriptions',
    )
    scheduled_change_at = models.DateTimeField(blank=True, null=True)
    failed_payment_count = models.PositiveIntegerField(default=0)
    payment_metadata = models.TextField(blank=True, default='')
    last_payment_reference = models.CharField(max_length=120, blank=True)
    last_invoiced_at = models.DateTimeField(blank=True, null=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Subscription'
        verbose_name_plural = 'Subscriptions'
        indexes = [
            models.Index(fields=['chama', 'status'], name='billing_sub_chama__status_idx'),
            models.Index(fields=['status', 'current_period_end'], name='billing_sub_status_p_idx'),
        ]
    
    def __str__(self):
        return f"{self.chama.name} - {self.plan.name} ({self.status})"
    
    @property
    def is_active(self):
        if self.status not in [self.TRIALING, self.ACTIVE]:
            return False
        if not self.current_period_end:
            return True
        return self.current_period_end > timezone.now()
    
    @property
    def days_remaining(self):
        if self.current_period_end:
            delta = self.current_period_end - timezone.now()
            return max(0, delta.days)
        return 0


class BillingRule(models.Model):
    """Configurable billing policy with optional chama override."""

    HARD_LOCK = 'hard_lock'
    SOFT_LOCK = 'soft_lock'

    ENFORCEMENT_CHOICES = [
        (HARD_LOCK, 'Hard Lock'),
        (SOFT_LOCK, 'Soft Lock'),
    ]

    name = models.CharField(max_length=80, default='default')
    chama = models.OneToOneField(
        Chama,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='billing_rule',
    )
    grace_period_days = models.PositiveIntegerField(default=7)
    enforcement_mode = models.CharField(
        max_length=20,
        choices=ENFORCEMENT_CHOICES,
        default=HARD_LOCK,
    )
    upgrade_approval_threshold = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    auto_renew_enabled = models.BooleanField(default=True)
    payment_retry_schedule = models.JSONField(default=list, blank=True)
    allow_enterprise_overrides = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['chama_id', 'name']
        indexes = [
            models.Index(fields=['chama', 'is_active']),
            models.Index(fields=['name', 'is_active']),
        ]

    def __str__(self):
        scope = self.chama.name if self.chama_id else 'global'
        return f'{scope} billing rule'


class SeatUsage(models.Model):
    """
    Tracks seat usage for a Chama
    Caches member count to avoid expensive queries
    """
    chama = models.OneToOneField(
        Chama,
        on_delete=models.CASCADE,
        related_name='seat_usage'
    )
    active_members_count = models.IntegerField(default=0)
    last_updated = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Seat Usage'
        verbose_name_plural = 'Seat Usages'
    
    def __str__(self):
        return f"{self.chama.name}: {self.active_members_count} seats used"


class FeatureOverride(models.Model):
    """
    Enterprise feature overrides
    Allows temporary or permanent feature enablement beyond plan limits
    """
    chama = models.ForeignKey(
        Chama,
        on_delete=models.CASCADE,
        related_name='feature_overrides'
    )
    feature_key = models.CharField(max_length=100)
    value = models.JSONField(default=dict)
    reason = models.TextField(blank=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_overrides',
    )
    
    class Meta:
        verbose_name = 'Feature Override'
        verbose_name_plural = 'Feature Overrides'
        unique_together = ['chama', 'feature_key']
    
    def __str__(self):
        return f"{self.chama.name} - {self.feature_key}"
    
    @property
    def is_expired(self):
        if self.expires_at:
            return self.expires_at < timezone.now()
        return False


class BillingEvent(models.Model):
    """
    Audit log for billing changes
    """
    # Event types
    PLAN_CHANGED = 'plan_changed'
    SUBSCRIPTION_CREATED = 'subscription_created'
    SUBSCRIPTION_CANCELED = 'subscription_canceled'
    PAYMENT_FAILED = 'payment_failed'
    PAYMENT_SUCCEEDED = 'payment_succeeded'
    SEAT_LIMIT_WARNING = 'seat_limit_warning'
    SEAT_LIMIT_EXCEEDED = 'seat_limit_exceeded'
    
    EVENT_CHOICES = [
        ('subscription_created', 'Subscription Created'),
        ('subscription_activated', 'Subscription Activated'),
        ('subscription_canceled', 'Subscription Canceled'),
        ('subscription_paused', 'Subscription Paused'),
        ('subscription_resumed', 'Subscription Resumed'),
        ('plan_changed', 'Plan Changed'),
        ('payment_succeeded', 'Payment Succeeded'),
        ('payment_failed', 'Payment Failed'),
        ('seat_limit_warning', 'Seat Limit Warning'),
        ('seat_limit_exceeded', 'Seat Limit Exceeded'),
        ('feature_override_created', 'Feature Override Created'),
        ('feature_override_removed', 'Feature Override Removed'),
    ]
    
    chama = models.ForeignKey(
        Chama,
        on_delete=models.CASCADE,
        related_name='billing_events'
    )
    event_type = models.CharField(max_length=50, choices=EVENT_CHOICES)
    details = models.JSONField(default=dict)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='billing_events'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Billing Event'
        verbose_name_plural = 'Billing Events'
        indexes = [
            models.Index(fields=['chama', 'created_at'], name='billing_bil_chama__creat_idx'),
        ]
    
    def __str__(self):
        return f"{self.chama.name} - {self.event_type}"


class UsageMetric(models.Model):
    """Tracks metered usage against plan limits."""

    MEMBERS = 'members'
    SMS = 'sms'
    OTP_SMS = 'otp_sms'
    STORAGE_MB = 'storage_mb'
    STK_PUSHES = 'stk_pushes'

    METRIC_CHOICES = [
        (MEMBERS, 'Members'),
        (SMS, 'SMS'),
        (OTP_SMS, 'OTP / Security SMS'),
        (STORAGE_MB, 'Storage (MB)'),
        (STK_PUSHES, 'M-Pesa STK Pushes'),
    ]

    chama = models.ForeignKey(
        Chama,
        on_delete=models.CASCADE,
        related_name='usage_metrics',
    )
    metric_key = models.CharField(max_length=40, choices=METRIC_CHOICES)
    used_quantity = models.PositiveIntegerField(default=0)
    limit_quantity = models.PositiveIntegerField(default=0)
    period_started_at = models.DateTimeField(default=timezone.now)
    period_ends_at = models.DateTimeField(null=True, blank=True)
    reset_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['metric_key']
        unique_together = ['chama', 'metric_key']
        indexes = [
            models.Index(fields=['chama', 'metric_key']),
            models.Index(fields=['period_ends_at']),
        ]

    def __str__(self):
        return f'{self.chama.name} {self.metric_key}: {self.used_quantity}/{self.limit_quantity}'


class BillingCredit(models.Model):
    """Stored monetary credit that can offset future invoices."""

    REFERRAL = 'referral'
    MANUAL = 'manual'
    ADJUSTMENT = 'adjustment'

    SOURCE_CHOICES = [
        (REFERRAL, 'Referral Reward'),
        (MANUAL, 'Manual Credit'),
        (ADJUSTMENT, 'Adjustment'),
    ]

    chama = models.ForeignKey(
        Chama,
        on_delete=models.CASCADE,
        related_name='billing_credits',
    )
    source_type = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=REFERRAL)
    source_reference = models.CharField(max_length=120, blank=True)
    description = models.CharField(max_length=255, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    remaining_amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='KES')
    expires_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at', 'id']
        indexes = [
            models.Index(fields=['chama', 'expires_at']),
            models.Index(fields=['source_type', 'source_reference']),
        ]

    def __str__(self):
        return (
            f'{self.chama.name} credit {self.total_amount} '
            f'({self.remaining_amount} remaining)'
        )

    @property
    def is_expired(self):
        return bool(self.expires_at and self.expires_at <= timezone.now())


class Invoice(models.Model):
    """Commercial invoice generated for plan changes and renewals."""

    DRAFT = 'draft'
    PENDING = 'pending'
    PAID = 'paid'
    FAILED = 'failed'
    VOID = 'void'
    REFUNDED = 'refunded'

    STATUS_CHOICES = [
        (DRAFT, 'Draft'),
        (PENDING, 'Pending'),
        (PAID, 'Paid'),
        (FAILED, 'Failed'),
        (VOID, 'Void'),
        (REFUNDED, 'Refunded'),
    ]

    invoice_number = models.CharField(max_length=40, unique=True)
    chama = models.ForeignKey(
        Chama,
        on_delete=models.CASCADE,
        related_name='invoices',
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoices',
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name='invoices',
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING,
    )
    provider = models.CharField(
        max_length=20,
        choices=Subscription.PROVIDER_CHOICES,
        default=Subscription.MANUAL,
    )
    currency = models.CharField(max_length=3, default='KES')
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    billing_period_start = models.DateTimeField(null=True, blank=True)
    billing_period_end = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_reference = models.CharField(max_length=120, blank=True)
    provider_transaction_id = models.CharField(max_length=120, blank=True)
    customer_email = models.EmailField(blank=True)
    pdf_file = models.FileField(upload_to='billing/invoices/', null=True, blank=True)
    metadata_encrypted = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['chama', 'status', 'created_at']),
            models.Index(fields=['provider_transaction_id']),
            models.Index(fields=['invoice_number']),
        ]

    def __str__(self):
        return self.invoice_number


class InvoiceLineItem(models.Model):
    """Detailed invoice row."""

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='line_items',
    )
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    total_price = models.DecimalField(max_digits=12, decimal_places=2)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.invoice.invoice_number} - {self.title}'


class BillingCreditAllocation(models.Model):
    """Tracks how billing credits are reserved and consumed per invoice."""

    RESERVED = 'reserved'
    APPLIED = 'applied'
    RELEASED = 'released'

    STATUS_CHOICES = [
        (RESERVED, 'Reserved'),
        (APPLIED, 'Applied'),
        (RELEASED, 'Released'),
    ]

    credit = models.ForeignKey(
        BillingCredit,
        on_delete=models.CASCADE,
        related_name='allocations',
    )
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='credit_allocations',
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=RESERVED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at', 'id']
        indexes = [
            models.Index(fields=['invoice', 'status']),
            models.Index(fields=['credit', 'status']),
        ]

    def __str__(self):
        return f'{self.invoice.invoice_number} -> {self.credit_id} ({self.amount})'


class BillingWebhookEvent(models.Model):
    """Raw webhook payload and idempotency trail for billing providers."""

    provider = models.CharField(max_length=20)
    event_type = models.CharField(max_length=80)
    external_event_id = models.CharField(max_length=120, blank=True)
    idempotency_key = models.CharField(max_length=160, unique=True)
    verified = models.BooleanField(default=False)
    signature_valid = models.BooleanField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=8, blank=True)
    chama = models.ForeignKey(
        Chama,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='billing_webhook_events',
    )
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='webhook_events',
    )
    payload = models.JSONField(default=dict, blank=True)
    headers = models.JSONField(default=dict, blank=True)
    processing_status = models.CharField(max_length=40, default='received')
    failure_reason = models.TextField(blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['provider', 'created_at']),
            models.Index(fields=['processing_status', 'created_at']),
            models.Index(fields=['external_event_id']),
        ]

    def __str__(self):
        return f'{self.provider}:{self.event_type}:{self.idempotency_key}'
