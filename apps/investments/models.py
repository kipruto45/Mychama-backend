# Investments Module Models
# Handles chama investments, portfolios, returns, and valuations

from django.db import models
from django.conf import settings
from apps.chama.models import Chama
from apps.accounts.models import User


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
