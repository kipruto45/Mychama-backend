# Investments Module Models
# Handles chama investments, portfolios, returns, and valuations

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama
from core.constants import CurrencyChoices
from core.models import BaseModel


class InvestmentType(models.TextChoices):
    FIXED_DEPOSIT = 'FIXED_DEPOSIT', 'Fixed Deposit'
    SACCO_SHARES = 'SACCO_SHARES', 'SACCO Shares'
    MMF = 'MMF', 'Money Market Fund'
    STOCKS = 'STOCKS', 'Stocks'
    LAND_PROJECT = 'LAND_PROJECT', 'Land Project'
    BIASHARA = 'BIASHARA', 'Biashara/Business'
    BONDS = 'BONDS', 'Government Bonds'
    OTHER = 'OTHER', 'Other'


class InvestmentStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Active'
    MATURED = 'MATURED', 'Matured'
    CLOSED = 'CLOSED', 'Closed'
    PENDING_APPROVAL = 'PENDING_APPROVAL', 'Pending Approval'


class TransactionType(models.TextChoices):
    BUY = 'BUY', 'Buy/Investment'
    TOPUP = 'TOPUP', 'Top Up'
    WITHDRAWAL = 'WITHDRAWAL', 'Withdrawal'
    FEE = 'FEE', 'Fee'
    INTEREST = 'INTEREST', 'Interest Credit'
    DIVIDEND = 'DIVIDEND', 'Dividend'


class ReturnType(models.TextChoices):
    DIVIDEND = 'DIVIDEND', 'Dividend'
    INTEREST = 'INTEREST', 'Interest'
    PROFIT = 'PROFIT', 'Profit'
    CAPITAL_GAIN = 'CAPITAL_GAIN', 'Capital Gain'


class ApprovalActionType(models.TextChoices):
    CREATE = 'CREATE', 'Create Investment'
    WITHDRAW = 'WITHDRAW', 'Withdraw Funds'
    MODIFY = 'MODIFY', 'Modify Terms'


class ApprovalStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    APPROVED = 'APPROVED', 'Approved'
    REJECTED = 'REJECTED', 'Rejected'


def generate_member_investment_reference() -> str:
    return f"INV-{uuid.uuid4().hex[:12].upper()}"


def generate_investment_transaction_reference() -> str:
    return f"ITX-{uuid.uuid4().hex[:12].upper()}"


def generate_investment_payout_reference() -> str:
    return f"IPO-{uuid.uuid4().hex[:12].upper()}"


def generate_investment_redemption_reference() -> str:
    return f"IRD-{uuid.uuid4().hex[:12].upper()}"


def generate_investment_utilization_reference() -> str:
    return f"IUT-{uuid.uuid4().hex[:12].upper()}"


class Investment(models.Model):
    """Represents an investment made by the chama"""
    chama = models.ForeignKey(Chama, on_delete=models.CASCADE, related_name='investments')
    
    # Basic info
    name = models.CharField(max_length=255)
    investment_type = models.CharField(max_length=50, choices=InvestmentType.choices)
    institution = models.CharField(max_length=255)  # Bank, SACCO, etc.
    
    # Financial details
    principal_amount = models.DecimalField(max_digits=14, decimal_places=2)
    current_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default='KES')
    
    # Dates
    start_date = models.DateField()
    maturity_date = models.DateField(null=True, blank=True)
    
    # Status
    status = models.CharField(max_length=30, choices=InvestmentStatus.choices, default=InvestmentStatus.PENDING_APPROVAL)
    
    # Documents/References
    account_number = models.CharField(max_length=100, blank=True)
    reference_number = models.CharField(max_length=100, blank=True)
    documents = models.JSONField(default=list, blank=True)  # List of document URLs
    
    # Notes
    notes = models.TextField(blank=True)
    
    # Distribution settings
    reinvest_returns = models.BooleanField(default=True)
    distribution_rule = models.JSONField(default=dict, blank=True)  # How to distribute returns
    
    # Metadata
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_investments')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.name} - {self.institution}"
    
    @property
    def roi(self):
        """Calculate Return on Investment"""
        if self.principal_amount and self.principal_amount > 0:
            total_returns = self.returns.aggregate(total=models.Sum('amount'))['total'] or 0
            return ((float(self.current_value) + float(total_returns) - float(self.principal_amount)) / float(self.principal_amount)) * 100
        return 0


class InvestmentTransaction(models.Model):
    """Tracks all transactions for an investment"""
    investment = models.ForeignKey(Investment, on_delete=models.CASCADE, related_name='transactions')
    transaction_type = models.CharField(max_length=20, choices=TransactionType.choices)
    
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    transaction_date = models.DateField()
    
    # Reference
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    
    # Recorded by
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='investment_transactions')
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-transaction_date']
    
    def __str__(self):
        return f"{self.get_transaction_type_display()} - {self.amount} on {self.investment.name}"


class InvestmentReturn(models.Model):
    """Tracks returns (dividends, interest, profits) from investments"""
    investment = models.ForeignKey(Investment, on_delete=models.CASCADE, related_name='returns')
    return_type = models.CharField(max_length=30, choices=ReturnType.choices)
    
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    date = models.DateField()
    notes = models.TextField(blank=True)
    
    # Reference
    reference = models.CharField(max_length=100, blank=True)
    
    # How this return was处理ed
    reinvested = models.BooleanField(default=False)
    distributed_to_members = models.BooleanField(default=False)
    distributed_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='recorded_investment_returns')
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-date']
    
    def __str__(self):
        return f"{self.get_return_type_display()} - {self.amount} from {self.investment.name}"


class InvestmentValuation(models.Model):
    """Tracks periodic valuations of investments"""
    investment = models.ForeignKey(Investment, on_delete=models.CASCADE, related_name='valuations')
    
    value = models.DecimalField(max_digits=14, decimal_places=2)
    date = models.DateField()
    notes = models.TextField(blank=True)
    
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='recorded_investment_valuations')
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-date']
        unique_together = ['investment', 'date']
    
    def __str__(self):
        return f"Valuation: {self.investment.name} = {self.value} on {self.date}"


class InvestmentApprovalRequest(models.Model):
    """Handles approval workflow for investments"""
    investment = models.OneToOneField(Investment, on_delete=models.CASCADE, related_name='approval_request')
    
    action_type = models.CharField(max_length=30, choices=ApprovalActionType.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    
    # Approval chain
    status = models.CharField(max_length=20, choices=ApprovalStatus.choices, default=ApprovalStatus.PENDING)
    
    # Approvers
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='investment_requests')
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_investments')
    approved_at = models.DateTimeField(null=True, blank=True)
    
    rejection_reason = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Approval for {self.get_action_type_display()} - {self.investment.name}"


class MemberInvestment(models.Model):
    """Optional: Track individual member contributions to investments"""
    investment = models.ForeignKey(Investment, on_delete=models.CASCADE, related_name='member_investments')
    member = models.ForeignKey(User, on_delete=models.CASCADE, related_name='investments')
    
    contribution_amount = models.DecimalField(max_digits=14, decimal_places=2)
    share_percentage = models.DecimalField(max_digits=5, decimal_places=2)  # e.g., 10.50 for 10.5%
    
    # Returns allocation
    returns_received = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    
    joined_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['investment', 'member']
    
    def __str__(self):
        return f"{self.member.get_full_name()} - {self.share_percentage}% of {self.investment.name}"


class InvestmentDistribution(models.Model):
    """Tracks distribution of investment returns to members"""
    investment = models.ForeignKey(Investment, on_delete=models.CASCADE, related_name='distributions')
    
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    distribution_date = models.DateField()
    
    # Distribution method
    method = models.CharField(max_length=50)  # PROPORTIONAL, EQUAL, CUSTOM
    
    # Status
    status = models.CharField(max_length=20, default='PENDING')  # PENDING, COMPLETED, CANCELLED
    
    notes = models.TextField(blank=True)
    
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_distributions')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-distribution_date']
    
    def __str__(self):
        return f"Distribution of {self.total_amount} for {self.investment.name}"


class InvestmentDistributionDetail(models.Model):
    """Individual member's share in a distribution"""
    distribution = models.ForeignKey(InvestmentDistribution, on_delete=models.CASCADE, related_name='details')
    member = models.ForeignKey(User, on_delete=models.CASCADE, related_name='investment_distributions')
    
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    share_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    
    paid = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        unique_together = ['distribution', 'member']
    
    def __str__(self):
        return f"{self.member.get_full_name()} gets {self.amount}"


class InvestmentProductCategory(models.TextChoices):
    FIXED_RETURN = "fixed_return", "Fixed Return"
    TARGET_BASED = "target_based", "Target Based"
    GROWTH = "growth", "Short-Term Growth"
    LOCKED = "locked", "Long-Term Locked"
    RECURRING = "recurring", "Recurring Auto-Invest"
    POOLED = "pooled", "Pooled Chama Investment"
    SPECIAL_OPPORTUNITY = "special_opportunity", "Special Opportunity"


class InvestmentProductStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"
    ARCHIVED = "archived", "Archived"


class InvestmentRiskLevel(models.TextChoices):
    LOW = "low", "Low"
    MODERATE = "moderate", "Moderate"
    HIGH = "high", "High"


class InvestmentReturnMethod(models.TextChoices):
    FIXED_RATE = "fixed_rate", "Fixed Rate"
    PROJECTED_RATE = "projected_rate", "Projected Rate"
    VARIABLE = "variable", "Variable"


class InvestmentFundingSource(models.TextChoices):
    WALLET = "wallet", "Wallet"
    MPESA = "mpesa", "M-Pesa"
    HYBRID = "hybrid", "Wallet + M-Pesa"


class MemberInvestmentPositionStatus(models.TextChoices):
    PENDING_FUNDING = "pending_funding", "Pending Funding"
    ACTIVE = "active", "Active"
    MATURED = "matured", "Matured"
    PARTIALLY_REDEEMED = "partially_redeemed", "Partially Redeemed"
    REDEEMED = "redeemed", "Redeemed"
    CANCELLED = "cancelled", "Cancelled"
    FAILED = "failed", "Failed"


class InvestmentPayoutFrequency(models.TextChoices):
    ONE_OFF = "one_off", "One Off"
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    ON_MATURITY = "on_maturity", "On Maturity"
    CUSTOM = "custom", "Custom"


class InvestmentTransactionRecordType(models.TextChoices):
    CREATED = "created", "Created"
    FUNDED = "funded", "Funded"
    RETURN_ACCRUAL = "return_accrual", "Return Accrual"
    RETURN_UTILIZATION = "return_utilization", "Return Utilization"
    REINVESTMENT = "reinvestment", "Reinvestment"
    PARTIAL_REDEMPTION = "partial_redemption", "Partial Redemption"
    FULL_REDEMPTION = "full_redemption", "Full Redemption"
    PAYOUT_WALLET = "payout_wallet", "Wallet Payout"
    PAYOUT_MPESA = "payout_mpesa", "M-Pesa Payout"
    FEE = "fee", "Fee"
    PENALTY = "penalty", "Penalty"
    REVERSAL = "reversal", "Reversal"


class InvestmentRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    REVERSED = "reversed", "Reversed"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class InvestmentReturnLedgerStatus(models.TextChoices):
    ACCRUED = "accrued", "Accrued"
    AVAILABLE = "available", "Available"
    UTILIZED = "utilized", "Utilized"
    REVERSED = "reversed", "Reversed"


class InvestmentRedemptionType(models.TextChoices):
    RETURNS_ONLY = "returns_only", "Returns Only"
    PARTIAL = "partial", "Partial Redemption"
    FULL = "full", "Full Redemption"


class InvestmentPayoutDestination(models.TextChoices):
    WALLET = "wallet", "Wallet"
    MPESA = "mpesa", "M-Pesa"
    REINVEST = "reinvest", "Reinvest"


class InvestmentPayoutKind(models.TextChoices):
    MATURITY_PAYOUT = "maturity_payout", "Maturity Payout"
    RETURNS_PAYOUT = "returns_payout", "Returns Payout"
    REDEMPTION_PAYOUT = "redemption_payout", "Redemption Payout"


class InvestmentProduct(BaseModel):
    chama = models.ForeignKey(
        Chama,
        on_delete=models.CASCADE,
        related_name="investment_products",
    )
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    description = models.TextField()
    category = models.CharField(
        max_length=40,
        choices=InvestmentProductCategory.choices,
        default=InvestmentProductCategory.FIXED_RETURN,
    )
    status = models.CharField(
        max_length=20,
        choices=InvestmentProductStatus.choices,
        default=InvestmentProductStatus.ACTIVE,
    )
    risk_level = models.CharField(
        max_length=20,
        choices=InvestmentRiskLevel.choices,
        default=InvestmentRiskLevel.MODERATE,
    )
    return_method = models.CharField(
        max_length=20,
        choices=InvestmentReturnMethod.choices,
        default=InvestmentReturnMethod.FIXED_RATE,
    )
    currency = models.CharField(
        max_length=3,
        choices=CurrencyChoices.choices,
        default=CurrencyChoices.KES,
    )
    minimum_amount = models.DecimalField(max_digits=14, decimal_places=2)
    maximum_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    expected_return_rate = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Annualized expected return rate in percent.",
    )
    projected_return_min_rate = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    projected_return_max_rate = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    term_days = models.PositiveIntegerField(default=30)
    lock_in_days = models.PositiveIntegerField(default=0)
    payout_frequency = models.CharField(
        max_length=20,
        choices=InvestmentPayoutFrequency.choices,
        default=InvestmentPayoutFrequency.ON_MATURITY,
    )
    liquidity_summary = models.CharField(max_length=255, blank=True)
    disclosure_title = models.CharField(max_length=160, blank=True)
    disclosure_body = models.TextField(blank=True)
    terms_summary = models.JSONField(default=list, blank=True)
    faq_items = models.JSONField(default=list, blank=True)
    trust_markers = models.JSONField(default=list, blank=True)
    partial_redemption_allowed = models.BooleanField(default=False)
    partial_redemption_min_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    returns_utilization_allowed = models.BooleanField(default=True)
    reinvestment_enabled = models.BooleanField(default=True)
    auto_reinvest_available = models.BooleanField(default=True)
    wallet_funding_enabled = models.BooleanField(default=True)
    mpesa_funding_enabled = models.BooleanField(default=True)
    hybrid_funding_enabled = models.BooleanField(default=False)
    wallet_payout_enabled = models.BooleanField(default=True)
    mpesa_payout_enabled = models.BooleanField(default=True)
    early_redemption_penalty_rate = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    management_fee_rate = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    withholding_tax_rate = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    active_member_limit = models.PositiveIntegerField(null=True, blank=True)
    usage_stats = models.JSONField(default=dict, blank=True)
    comparison_highlights = models.JSONField(default=list, blank=True)
    suitability_notes = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["chama", "code"],
                name="uniq_investment_product_code_per_chama",
            ),
            models.CheckConstraint(
                condition=models.Q(minimum_amount__gt=Decimal("0.00")),
                name="investment_product_min_amount_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(expected_return_rate__gte=Decimal("0.00")),
                name="investment_product_return_rate_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(lock_in_days__gte=0) & models.Q(term_days__gt=0),
                name="investment_product_term_days_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "status"]),
            models.Index(fields=["chama", "category"]),
            models.Index(fields=["risk_level", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    @property
    def is_active(self) -> bool:
        return self.status == InvestmentProductStatus.ACTIVE


class MemberInvestmentPosition(BaseModel):
    chama = models.ForeignKey(
        Chama,
        on_delete=models.CASCADE,
        related_name="member_investment_positions",
    )
    product = models.ForeignKey(
        InvestmentProduct,
        on_delete=models.PROTECT,
        related_name="member_positions",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="member_investment_positions",
    )
    reference = models.CharField(
        max_length=40,
        unique=True,
        default=generate_member_investment_reference,
    )
    status = models.CharField(
        max_length=24,
        choices=MemberInvestmentPositionStatus.choices,
        default=MemberInvestmentPositionStatus.PENDING_FUNDING,
    )
    funding_source = models.CharField(
        max_length=20,
        choices=InvestmentFundingSource.choices,
    )
    currency = models.CharField(
        max_length=3,
        choices=CurrencyChoices.choices,
        default=CurrencyChoices.KES,
    )
    principal_amount = models.DecimalField(max_digits=14, decimal_places=2)
    wallet_funded_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    mpesa_funded_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    current_value = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    accrued_returns = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    available_returns = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    realized_returns = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    redeemed_principal = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    total_fees_charged = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    total_penalties_charged = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    expected_value_at_maturity = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    auto_reinvest = models.BooleanField(default=False)
    is_recurring_plan = models.BooleanField(default=False)
    recurring_frequency = models.CharField(max_length=20, blank=True)
    next_recurring_run_at = models.DateTimeField(null=True, blank=True)
    beneficiary_phone = models.CharField(max_length=16, blank=True)
    latest_status_note = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    funded_at = models.DateTimeField(null=True, blank=True)
    maturity_date = models.DateTimeField(null=True, blank=True)
    next_payout_date = models.DateTimeField(null=True, blank=True)
    last_accrual_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(principal_amount__gt=Decimal("0.00")),
                name="member_investment_principal_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(current_value__gte=Decimal("0.00"))
                & models.Q(accrued_returns__gte=Decimal("0.00"))
                & models.Q(available_returns__gte=Decimal("0.00"))
                & models.Q(realized_returns__gte=Decimal("0.00"))
                & models.Q(redeemed_principal__gte=Decimal("0.00")),
                name="member_investment_amounts_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["chama", "member", "status"]),
            models.Index(fields=["product", "status"]),
            models.Index(fields=["maturity_date", "status"]),
            models.Index(fields=["next_payout_date", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.reference} - {self.member} - {self.product.name}"

    @property
    def redemption_eligible(self) -> bool:
        if self.status not in {
            MemberInvestmentPositionStatus.ACTIVE,
            MemberInvestmentPositionStatus.MATURED,
            MemberInvestmentPositionStatus.PARTIALLY_REDEEMED,
        }:
            return False
        if self.product.lock_in_days <= 0 or self.funded_at is None:
            return True
        return timezone.now() >= self.funded_at + timezone.timedelta(days=self.product.lock_in_days)

    @property
    def is_matured(self) -> bool:
        return bool(self.maturity_date and timezone.now() >= self.maturity_date)


class InvestmentTransactionRecord(BaseModel):
    investment = models.ForeignKey(
        MemberInvestmentPosition,
        on_delete=models.CASCADE,
        related_name="transactions_v2",
    )
    product = models.ForeignKey(
        InvestmentProduct,
        on_delete=models.PROTECT,
        related_name="transactions_v2",
    )
    chama = models.ForeignKey(
        Chama,
        on_delete=models.CASCADE,
        related_name="investment_transactions_v2",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="investment_transactions_v2",
    )
    transaction_type = models.CharField(
        max_length=32,
        choices=InvestmentTransactionRecordType.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=InvestmentRequestStatus.choices,
        default=InvestmentRequestStatus.COMPLETED,
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    fee_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    penalty_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    net_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    currency = models.CharField(
        max_length=3,
        choices=CurrencyChoices.choices,
        default=CurrencyChoices.KES,
    )
    reference = models.CharField(
        max_length=40,
        unique=True,
        default=generate_investment_transaction_reference,
    )
    external_reference = models.CharField(max_length=120, blank=True)
    destination = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(default=timezone.now)
    payment_intent = models.ForeignKey(
        "payments.PaymentIntent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="investment_transactions_v2",
    )
    wallet = models.ForeignKey(
        "finance.Wallet",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="investment_transactions_v2",
    )

    class Meta:
        ordering = ["-processed_at", "-created_at"]
        indexes = [
            models.Index(fields=["investment", "transaction_type"]),
            models.Index(fields=["member", "processed_at"]),
            models.Index(fields=["status", "processed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.reference} - {self.transaction_type}"


class InvestmentReturnLedger(BaseModel):
    investment = models.ForeignKey(
        MemberInvestmentPosition,
        on_delete=models.CASCADE,
        related_name="return_ledgers",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(
        max_length=16,
        choices=InvestmentReturnLedgerStatus.choices,
        default=InvestmentReturnLedgerStatus.ACCRUED,
    )
    gross_returns = models.DecimalField(max_digits=14, decimal_places=2)
    management_fee = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    withholding_tax = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    net_returns = models.DecimalField(max_digits=14, decimal_places=2)
    available_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    utilized_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    accrued_at = models.DateTimeField(default=timezone.now)
    available_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-period_end", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(gross_returns__gte=Decimal("0.00"))
                & models.Q(net_returns__gte=Decimal("0.00"))
                & models.Q(available_amount__gte=Decimal("0.00"))
                & models.Q(utilized_amount__gte=Decimal("0.00")),
                name="investment_return_ledger_amounts_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["investment", "status"]),
            models.Index(fields=["period_end", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.investment.reference} returns {self.period_end}"


class InvestmentPayout(BaseModel):
    investment = models.ForeignKey(
        MemberInvestmentPosition,
        on_delete=models.CASCADE,
        related_name="payouts",
    )
    kind = models.CharField(
        max_length=24,
        choices=InvestmentPayoutKind.choices,
    )
    destination = models.CharField(
        max_length=20,
        choices=InvestmentPayoutDestination.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=InvestmentRequestStatus.choices,
        default=InvestmentRequestStatus.PENDING,
    )
    reference = models.CharField(
        max_length=40,
        unique=True,
        default=generate_investment_payout_reference,
    )
    gross_amount = models.DecimalField(max_digits=14, decimal_places=2)
    fee_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    penalty_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    net_amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(
        max_length=3,
        choices=CurrencyChoices.choices,
        default=CurrencyChoices.KES,
    )
    destination_phone = models.CharField(max_length=16, blank=True)
    failure_reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    payment_intent = models.ForeignKey(
        "payments.PaymentIntent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="investment_payouts",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["investment", "status"]),
            models.Index(fields=["destination", "status"]),
        ]

    def __str__(self) -> str:
        return self.reference


class InvestmentRedemptionRequest(BaseModel):
    investment = models.ForeignKey(
        MemberInvestmentPosition,
        on_delete=models.CASCADE,
        related_name="redemption_requests",
    )
    redemption_type = models.CharField(
        max_length=20,
        choices=InvestmentRedemptionType.choices,
    )
    destination = models.CharField(
        max_length=20,
        choices=InvestmentPayoutDestination.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=InvestmentRequestStatus.choices,
        default=InvestmentRequestStatus.PENDING,
    )
    reference = models.CharField(
        max_length=40,
        unique=True,
        default=generate_investment_redemption_reference,
    )
    requested_amount = models.DecimalField(max_digits=14, decimal_places=2)
    principal_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    profit_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    fee_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    penalty_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    net_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    beneficiary_phone = models.CharField(max_length=16, blank=True)
    reason = models.TextField(blank=True)
    failure_reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processed_investment_redemptions",
    )
    payout = models.OneToOneField(
        InvestmentPayout,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="redemption_request",
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["investment", "status"]),
        ]

    def __str__(self) -> str:
        return self.reference


class InvestmentUtilizationAction(BaseModel):
    investment = models.ForeignKey(
        MemberInvestmentPosition,
        on_delete=models.CASCADE,
        related_name="utilization_actions",
    )
    action_type = models.CharField(
        max_length=20,
        choices=InvestmentPayoutDestination.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=InvestmentRequestStatus.choices,
        default=InvestmentRequestStatus.PENDING,
    )
    reference = models.CharField(
        max_length=40,
        unique=True,
        default=generate_investment_utilization_reference,
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    fee_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    net_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    beneficiary_phone = models.CharField(max_length=16, blank=True)
    failure_reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    payout = models.OneToOneField(
        InvestmentPayout,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="utilization_action",
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["investment", "status"]),
            models.Index(fields=["action_type", "status"]),
        ]

    def __str__(self) -> str:
        return self.reference


class InvestmentSnapshot(BaseModel):
    investment = models.ForeignKey(
        MemberInvestmentPosition,
        on_delete=models.CASCADE,
        related_name="snapshots",
    )
    snapshot_date = models.DateField()
    principal_amount = models.DecimalField(max_digits=14, decimal_places=2)
    current_value = models.DecimalField(max_digits=14, decimal_places=2)
    accrued_returns = models.DecimalField(max_digits=14, decimal_places=2)
    available_returns = models.DecimalField(max_digits=14, decimal_places=2)
    realized_returns = models.DecimalField(max_digits=14, decimal_places=2)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-snapshot_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["investment", "snapshot_date"],
                name="uniq_investment_snapshot_per_day",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.investment.reference} @ {self.snapshot_date}"
