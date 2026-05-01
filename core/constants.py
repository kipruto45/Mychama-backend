from django.db import models

DEFAULT_TIMEZONE = "Africa/Nairobi"


class RoleChoices(models.TextChoices):
    CHAMA_ADMIN = "CHAMA_ADMIN", "Chama Admin"
    TREASURER = "TREASURER", "Treasurer"
    SECRETARY = "SECRETARY", "Secretary"
    MEMBER = "MEMBER", "Member"
    AUDITOR = "AUDITOR", "Auditor"


class StatusChoices(models.TextChoices):
    PENDING = "pending", "Pending"
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"
    SUSPENDED = "suspended", "Suspended"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class StandardLifecycleStatus(models.TextChoices):
    INITIATED = "initiated", "Initiated"
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    EXPIRED = "expired", "Expired"


class LoanLifecycleStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    REVIEW = "review", "Review"
    APPROVED = "approved", "Approved"
    DISBURSING = "disbursing", "Disbursing"
    DISBURSED = "disbursed", "Disbursed"
    ACTIVE = "active", "Active"
    PAID = "paid", "Paid"
    CLOSED = "closed", "Closed"
    DEFAULTED = "defaulted", "Defaulted"
    REJECTED = "rejected", "Rejected"


class MethodChoices(models.TextChoices):
    CASH = "cash", "Cash"
    MPESA = "mpesa", "M-Pesa"
    BANK_TRANSFER = "bank_transfer", "Bank Transfer"
    WALLET = "wallet", "Wallet"
    CARD = "card", "Card"
    OTHER = "other", "Other"


class CurrencyChoices(models.TextChoices):
    KES = "KES", "Kenyan Shilling"
    USD = "USD", "US Dollar"
    EUR = "EUR", "Euro"
    GBP = "GBP", "British Pound"


ROLE_CHOICES = RoleChoices.choices
STATUS_CHOICES = StatusChoices.choices
METHOD_CHOICES = MethodChoices.choices
CURRENCY_CHOICES = CurrencyChoices.choices
STANDARD_LIFECYCLE_CHOICES = StandardLifecycleStatus.choices
LOAN_LIFECYCLE_CHOICES = LoanLifecycleStatus.choices

# Legacy constants retained for compatibility with domain apps.
CHAMA_ROLES = ROLE_CHOICES
GLOBAL_ROLES = [("SUPERADMIN", "Super Admin")]
PAYMENT_METHODS = METHOD_CHOICES
LOAN_STATUSES = [
    ("requested", "Requested"),
    ("approved", "Approved"),
    ("disbursing", "Disbursing"),
    ("disbursed", "Disbursed"),
    ("active", "Active"),
    ("cleared", "Cleared"),
    ("defaulted", "Defaulted"),
    ("rejected", "Rejected"),
]
PENALTY_STATUSES = [
    ("unpaid", "Unpaid"),
    ("paid", "Paid"),
    ("waived", "Waived"),
]
CURRENCY_KES = CurrencyChoices.KES

CONTRIBUTION_FREQUENCIES = [
    ("weekly", "Weekly"),
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("annually", "Annually"),
    ("one_off", "One Off"),
]
MEETING_TYPES = [
    ("regular", "Regular"),
    ("special", "Special"),
    ("agm", "Annual General Meeting"),
    ("emergency", "Emergency"),
]
ATTENDANCE_STATUSES = [
    ("present", "Present"),
    ("absent", "Absent"),
    ("excused", "Excused"),
]
INSTALLMENT_STATUSES = [
    ("due", "Due"),
    ("paid", "Paid"),
    ("overdue", "Overdue"),
    ("partial", "Partially Paid"),
]
LEDGER_ENTRY_TYPES = [
    ("contribution", "Contribution"),
    ("loan_disbursement", "Loan Disbursement"),
    ("repayment", "Repayment"),
    ("penalty", "Penalty"),
    ("adjustment", "Adjustment"),
]
LEDGER_DIRECTIONS = [
    ("debit", "Debit"),
    ("credit", "Credit"),
]
MPESA_STATUSES = [
    ("initiated", "Initiated"),
    ("pending_callback", "Pending Callback"),
    ("success", "Success"),
    ("failed", "Failed"),
    ("cancelled", "Cancelled"),
]
NOTIFICATION_TYPES = [
    ("general_announcement", "General Announcement"),
    ("contribution_reminder", "Contribution Reminder"),
    ("meeting_notification", "Meeting Notification"),
    ("payment_confirmation", "Payment Confirmation"),
    ("loan_update", "Loan Update"),
    ("system", "System"),
]
NOTIFICATION_PRIORITIES = [
    ("low", "Low"),
    ("normal", "Normal"),
    ("high", "High"),
]
NOTIFICATION_STATUSES = [
    ("pending", "Pending"),
    ("processing", "Processing"),
    ("sent", "Sent"),
    ("failed", "Failed"),
    ("cancelled", "Cancelled"),
]
