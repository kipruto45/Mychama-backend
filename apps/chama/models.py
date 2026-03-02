import hmac
import secrets
import uuid
import hashlib

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.constants import CurrencyChoices
from core.models import BaseModel


class ChamaStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"


class ChamaType(models.TextChoices):
    SAVINGS = "savings", "Savings"
    INVESTMENT = "investment", "Investment"
    WELFARE = "welfare", "Welfare"
    MIXED = "mixed", "Mixed"


class MembershipRole(models.TextChoices):
    SUPERADMIN = "SUPERADMIN", "Super Admin"
    ADMIN = "ADMIN", "Admin"
    CHAMA_ADMIN = "CHAMA_ADMIN", "Chama Admin"
    TREASURER = "TREASURER", "Treasurer"
    SECRETARY = "SECRETARY", "Secretary"
    MEMBER = "MEMBER", "Member"
    AUDITOR = "AUDITOR", "Auditor"


class MemberStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    EXITED = "exited", "Exited"


class JoinApprovalPolicy(models.TextChoices):
    AUTO = "auto", "Auto Approve"
    ADMIN = "admin", "Admin Approval"
    SECRETARY = "secretary", "Secretary Approval"


class JoinCodeMode(models.TextChoices):
    AUTO_JOIN = "auto_join", "Auto Join"
    APPROVAL_REQUIRED = "approval_required", "Approval Required"


class MeetingFrequency(models.TextChoices):
    WEEKLY = "weekly", "Weekly"
    BIWEEKLY = "biweekly", "Bi-weekly"
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"


class ContributionFrequency(models.TextChoices):
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"
    BIWEEKLY = "biweekly", "Bi-weekly"
    MONTHLY = "monthly", "Monthly"


class ContributionType(models.TextChoices):
    FIXED = "fixed", "Fixed Amount"
    FLEXIBLE = "flexible", "Flexible"


class MajorityType(models.TextChoices):
    SIMPLE = "simple", "Simple Majority (>50%)"
    SUPER = "super", "Super Majority (>66%)"
    UNANIMOUS = "unanimous", "Unanimous"


class InterestModel(models.TextChoices):
    FLAT = "flat", "Flat Rate"
    REDUCING = "reducing", "Reducing Balance"
    FEE = "fee", "Fee Based"


class Chama(BaseModel):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    county = models.CharField(max_length=120, blank=True)
    subcounty = models.CharField(max_length=120, blank=True)
    referred_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="referred_chamas",
    )
    referral_code_used = models.CharField(max_length=16, blank=True)
    referral_applied_at = models.DateTimeField(null=True, blank=True)
    currency = models.CharField(
        max_length=3,
        choices=CurrencyChoices.choices,
        default=CurrencyChoices.KES,
    )
    status = models.CharField(
        max_length=20,
        choices=ChamaStatus.choices,
        default=ChamaStatus.ACTIVE,
    )
    chama_type = models.CharField(
        max_length=20,
        choices=ChamaType.choices,
        default=ChamaType.SAVINGS,
        blank=True,
    )
    
    # Join Settings
    join_enabled = models.BooleanField(default=True)
    join_code = models.CharField(max_length=12, unique=True, blank=True)
    join_code_expires_at = models.DateTimeField(null=True, blank=True)
    join_mode = models.CharField(
        max_length=24,
        choices=JoinCodeMode.choices,
        default=JoinCodeMode.APPROVAL_REQUIRED,
    )
    allow_public_join = models.BooleanField(default=False)
    require_approval = models.BooleanField(default=True)
    
    # Settings
    max_members = models.PositiveIntegerField(default=100)
    
    # Wizard completion tracking
    setup_completed = models.BooleanField(default=False)
    setup_step = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["status"]),
            models.Index(fields=["county", "subcounty"]),
            models.Index(fields=["join_code"]),
            models.Index(fields=["chama_type"]),
        ]

    def __str__(self):
        return self.name

    def _build_unique_join_code(self):
        """Generate a unique join code before first save."""
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        while True:
            first = "".join(secrets.choice(alphabet) for _ in range(4))
            second = "".join(secrets.choice(alphabet) for _ in range(4))
            code = f"{first}-{second}"
            if not Chama.objects.filter(join_code=code).exclude(pk=self.pk).exists():
                return code

    def apply_join_mode(self, join_mode: str | None = None):
        resolved_mode = join_mode or self.join_mode or JoinCodeMode.APPROVAL_REQUIRED
        self.join_mode = resolved_mode
        if resolved_mode == JoinCodeMode.AUTO_JOIN:
            self.allow_public_join = True
            self.require_approval = False
        else:
            self.allow_public_join = False
            self.require_approval = True
        return self.join_mode

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        generated_join_code = False

        # Generate join_code if not set or empty
        if self.join_enabled and (not self.join_code or self.join_code == ''):
            self.join_code = self._build_unique_join_code()
            generated_join_code = True

        if self.join_mode:
            self.apply_join_mode(self.join_mode)

        if generated_join_code and not self.join_code_expires_at:
            self.join_code_expires_at = timezone.now() + timezone.timedelta(days=30)

        if not self.join_enabled:
            self.join_code_expires_at = None

        if update_fields is not None:
            normalized_update_fields = set(update_fields)
            if generated_join_code:
                normalized_update_fields |= {"join_code", "join_code_expires_at"}
            if not self.join_enabled:
                normalized_update_fields.add("join_code_expires_at")
            kwargs["update_fields"] = normalized_update_fields

        super().save(*args, **kwargs)

    def generate_join_code(self):
        """Generate a unique join code for the chama."""
        self.join_enabled = True
        self.join_code = self._build_unique_join_code()
        self.join_code_expires_at = timezone.now() + timezone.timedelta(days=30)
        self.save(update_fields=["join_enabled", "join_code", "join_code_expires_at"])
        return self.join_code


class Membership(BaseModel):
    """Membership in a Chama."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    chama = models.ForeignKey(
        Chama, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(
        max_length=20,
        choices=MembershipRole.choices,
        default=MembershipRole.MEMBER,
    )
    status = models.CharField(
        max_length=20,
        choices=MemberStatus.choices,
        default=MemberStatus.PENDING,
    )
    
    # Legacy fields from original migration
    is_active = models.BooleanField(default=False)
    is_approved = models.BooleanField(default=False)
    joined_at = models.DateTimeField(default=timezone.now)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_chama_memberships",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.TextField(blank=True)
    exited_at = models.DateTimeField(null=True, blank=True)
    exit_reason = models.TextField(blank=True)
    
    # Financial (from BaseModel)
    
    # Delegation
    delegated_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="delegations",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["chama", "status"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["chama", "role"]),
            models.Index(fields=["is_approved", "status"]),
            models.Index(fields=["chama", "is_active", "is_approved"]),
            models.Index(fields=["user", "is_active", "is_approved"]),
            models.Index(fields=["role"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "chama"],
                name="uniq_user_chama_membership",
            ),
            models.CheckConstraint(
                condition=(~models.Q(status=MemberStatus.ACTIVE) | models.Q(is_approved=True)),
                name="active_requires_approval",
            ),
        ]

    def __str__(self):
        return f"{self.user.full_name} - {self.chama.name} ({self.role})"

    @property
    def is_active_member(self):
        return self.status == MemberStatus.ACTIVE


class RoleDelegation(BaseModel):
    """Temporary role delegation between members."""
    chama = models.ForeignKey(
        Chama,
        on_delete=models.CASCADE,
        related_name="role_delegations",
    )
    delegator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="role_delegations_given",
    )
    delegatee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="role_delegations_received",
    )
    role = models.CharField(
        max_length=20,
        choices=MembershipRole.choices,
    )
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.TextField(blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="revoked_role_delegations",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["chama", "role", "is_active"]),
            models.Index(fields=["delegatee", "is_active", "ends_at"]),
            models.Index(fields=["delegator", "is_active", "ends_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="role_delegation_end_after_start",
            ),
        ]

    def __str__(self):
        return f"{self.delegator} -> {self.delegatee} ({self.role})"


class InviteStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"


def _generate_public_token(length: int = 24) -> str:
    return secrets.token_urlsafe(length)[:length]


def _token_signature(scope: str, token_value: str) -> str:
    digest = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        f"{scope}:{token_value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:32]


def _split_presented_token(presented_token: str) -> tuple[str, str]:
    raw_value = str(presented_token or "").strip()
    if "." not in raw_value:
        return raw_value, ""
    public_token, signature = raw_value.rsplit(".", 1)
    return public_token.strip(), signature.strip()


class Invite(BaseModel):
    """Invite to join a Chama (one-time use)."""
    chama = models.ForeignKey(
        Chama, on_delete=models.CASCADE, related_name="invites"
    )
    identifier = models.CharField(max_length=255, help_text="Invitee phone or email")
    token = models.CharField(max_length=64, unique=True, db_index=True)
    requires_signature = models.BooleanField(default=False)
    # Legacy/optional fields for backward compatibility
    phone = models.CharField(max_length=16, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    role = models.CharField(
        max_length=20,
        choices=MembershipRole.choices,
        default=MembershipRole.MEMBER,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=InviteStatus.choices,
        default=InviteStatus.PENDING,
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_chama_invites",
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accepted_chama_invites",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    max_uses = models.PositiveIntegerField(default=1)
    use_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "expires_at"]),
            models.Index(fields=["chama", "status"]),
        ]

    def __str__(self):
        return f"Invite to {self.chama.name} ({self.identifier})"

    @staticmethod
    def generate_token():
        return _generate_public_token(24)

    def build_presented_token(self) -> str:
        if not self.requires_signature:
            return self.token
        return f"{self.token}.{_token_signature('invite', self.token)}"

    def matches_presented_token(self, presented_token: str) -> bool:
        public_token, signature = _split_presented_token(presented_token)
        if self.requires_signature:
            return (
                public_token == self.token
                and bool(signature)
                and hmac.compare_digest(signature, _token_signature("invite", self.token))
            )
        return presented_token == self.token

    @classmethod
    def resolve_presented_token(cls, presented_token: str, queryset=None):
        public_token, signature = _split_presented_token(presented_token)
        if queryset is None:
            queryset = cls.objects.all()

        if signature:
            candidate = queryset.filter(token=public_token, requires_signature=True).first()
            if candidate and candidate.matches_presented_token(presented_token):
                return candidate
            return None

        return queryset.filter(token=public_token, requires_signature=False).first()

    def is_valid(self):
        if self.status != InviteStatus.PENDING:
            return False
        if self.expires_at < timezone.now():
            return False
        if self.use_count >= self.max_uses:
            return False
        return True

    def save(self, *args, **kwargs):
        if self._state.adding and not self.requires_signature:
            self.requires_signature = True
        if not self.token:
            self.token = self.generate_token()
        super().save(*args, **kwargs)


class MembershipRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    NEEDS_INFO = "needs_info", "Needs More Information"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"


class MembershipRequestSource(models.TextChoices):
    PUBLIC_JOIN = "public_join", "Public Join"
    JOIN_CODE = "join_code", "Join Code"
    INVITE_LINK = "invite_link", "Invite Link"


class MembershipRequest(BaseModel):
    """Request to join a Chama."""
    chama = models.ForeignKey(
        Chama, on_delete=models.CASCADE, related_name="membership_requests"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="membership_requests",
    )
    status = models.CharField(
        max_length=20,
        choices=MembershipRequestStatus.choices,
        default=MembershipRequestStatus.PENDING,
    )
    requested_via = models.CharField(
        max_length=20,
        choices=MembershipRequestSource.choices,
        default=MembershipRequestSource.PUBLIC_JOIN,
    )
    invite_link = models.ForeignKey(
        "InviteLink",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="membership_requests",
    )
    phone_verified_at_approval = models.DateTimeField(null=True, blank=True)
    request_note = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_info = models.CharField(max_length=255, blank=True)
    ai_decision = models.CharField(max_length=30, blank=True)
    ai_confidence = models.FloatField(null=True, blank=True)
    ai_risk_score = models.IntegerField(null=True, blank=True)
    ai_recommendation = models.JSONField(default=dict, blank=True)
    ai_reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_membership_requests",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["chama", "status"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["status", "reviewed_by"]),
            models.Index(fields=["requested_via", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "chama"],
                condition=models.Q(status=MembershipRequestStatus.PENDING),
                name="unique_pending_request_per_user_chama",
            ),
        ]

    def __str__(self):
        return f"{self.user.full_name} → {self.chama.name} ({self.status})"


class InviteLink(BaseModel):
    """Reusable invite link for a Chama."""
    chama = models.ForeignKey(
        Chama, on_delete=models.CASCADE, related_name="invite_links"
    )
    token = models.CharField(max_length=64, unique=True, db_index=True)
    requires_signature = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_invite_links",
    )
    preassigned_role = models.CharField(
        max_length=20,
        choices=MembershipRole.choices,
        blank=True,
    )
    approval_required = models.BooleanField(default=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    current_uses = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.TextField(blank=True)
    restricted_phone = models.CharField(max_length=16, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["token"]),
            models.Index(fields=["chama", "is_active"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return f"Link to {self.chama.name}"

    @staticmethod
    def generate_token():
        return _generate_public_token(24)

    def build_presented_token(self) -> str:
        if not self.requires_signature:
            return self.token
        return f"{self.token}.{_token_signature('invite_link', self.token)}"

    def matches_presented_token(self, presented_token: str) -> bool:
        public_token, signature = _split_presented_token(presented_token)
        if self.requires_signature:
            return (
                public_token == self.token
                and bool(signature)
                and hmac.compare_digest(
                    signature,
                    _token_signature("invite_link", self.token),
                )
            )
        return presented_token == self.token

    @classmethod
    def resolve_presented_token(cls, presented_token: str, queryset=None):
        public_token, signature = _split_presented_token(presented_token)
        if queryset is None:
            queryset = cls.objects.all()

        if signature:
            candidate = queryset.filter(token=public_token, requires_signature=True).first()
            if candidate and candidate.matches_presented_token(presented_token):
                return candidate
            return None

        return queryset.filter(token=public_token, requires_signature=False).first()

    @property
    def code(self):
        return self.token

    @code.setter
    def code(self, value):
        self.token = value

    @property
    def use_count(self):
        return self.current_uses

    @use_count.setter
    def use_count(self, value):
        self.current_uses = value

    @property
    def role(self):
        return self.preassigned_role or MembershipRole.MEMBER

    def is_valid(self):
        if not self.is_active:
            return False
        if self.expires_at < timezone.now():
            return False
        if self.max_uses and self.current_uses >= self.max_uses:
            return False
        return True

    def save(self, *args, **kwargs):
        if self._state.adding and not self.requires_signature:
            self.requires_signature = True
        if not self.token:
            self.token = self.generate_token()
        super().save(*args, **kwargs)


# ============================================
# NEW MODELS - Chama Settings & Configuration
# ============================================

class ChamaSettings(BaseModel):
    """Governance and configuration settings for a Chama."""
    chama = models.OneToOneField(
        Chama, on_delete=models.CASCADE, related_name="settings"
    )
    
    # Join Policy
    join_approval_policy = models.CharField(
        max_length=20,
        choices=JoinApprovalPolicy.choices,
        default=JoinApprovalPolicy.ADMIN,
    )
    
    # Meeting Settings
    meeting_frequency = models.CharField(
        max_length=20,
        choices=MeetingFrequency.choices,
        default=MeetingFrequency.MONTHLY,
    )
    meeting_day = models.PositiveIntegerField(default=5)  # Day of month or week (1-7)
    meeting_time = models.TimeField(null=True, blank=True)
    
    # Voting Rules
    voting_quorum_percent = models.PositiveIntegerField(default=50)
    voting_majority = models.CharField(
        max_length=20,
        choices=MajorityType.choices,
        default=MajorityType.SIMPLE,
    )
    
    # Late Payment
    grace_period_days = models.PositiveIntegerField(default=2)
    late_penalty_type = models.CharField(max_length=20, blank=True)  # flat or percent
    late_penalty_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    class Meta:
        verbose_name = "Chama Settings"
        verbose_name_plural = "Chama Settings"


class ContributionPlan(BaseModel):
    """Contribution/deposit plan for a Chama."""
    chama = models.ForeignKey(
        Chama, on_delete=models.CASCADE, related_name="contribution_plans"
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    
    # Amount Settings
    contribution_type = models.CharField(
        max_length=20,
        choices=ContributionType.choices,
        default=ContributionType.FIXED,
    )
    fixed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    min_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    max_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    # Frequency
    frequency = models.CharField(
        max_length=20,
        choices=ContributionFrequency.choices,
        default=ContributionFrequency.WEEKLY,
    )
    due_day = models.PositiveIntegerField(default=5)  # Day of week (1-7) or day of month
    
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        unique_together = ["chama", "name"]

    def __str__(self):
        return f"{self.chama.name} - {self.name}"


class LoanPolicy(BaseModel):
    """Loan policy configuration for a Chama."""
    chama = models.OneToOneField(
        Chama, on_delete=models.CASCADE, related_name="loan_policy"
    )
    
    # Enable/Disable
    loans_enabled = models.BooleanField(default=True)
    
    # Eligibility
    min_contribution_cycles = models.PositiveIntegerField(default=3)
    max_active_loans = models.PositiveIntegerField(default=1)
    loan_cap_multiplier = models.DecimalField(
        max_digits=5, decimal_places=2, default=3.0,
        help_text="Max loan = this x total contributions"
    )
    
    # Interest
    interest_model = models.CharField(
        max_length=20,
        choices=InterestModel.choices,
        default=InterestModel.FLAT,
    )
    interest_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=10,
        help_text="Annual interest rate %"
    )
    
    # Approval
    require_guarantors = models.BooleanField(default=True)
    min_guarantors = models.PositiveIntegerField(default=1)
    require_treasurer_approval = models.BooleanField(default=True)
    require_admin_approval = models.BooleanField(default=True)
    require_committee_vote = models.BooleanField(default=False)
    
    # Penalties
    penalty_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=2,
        help_text="Late payment penalty % per month"
    )
    
    # Repayment
    min_repayment_period = models.PositiveIntegerField(default=1)  # months
    max_repayment_period = models.PositiveIntegerField(default=12)  # months
    allow_early_repayment = models.BooleanField(default=True)
    early_repayment_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0
    )

    class Meta:
        verbose_name = "Loan Policy"
        verbose_name_plural = "Loan Policies"


class ExpensePolicy(BaseModel):
    """Expense and withdrawal policy for a Chama."""
    chama = models.OneToOneField(
        Chama, on_delete=models.CASCADE, related_name="expense_policy"
    )
    
    # Spending Categories
    allow_withdrawals = models.BooleanField(default=True)
    allow_expenses = models.BooleanField(default=True)
    
    # Approval Thresholds
    treasurer_admin_threshold = models.DecimalField(
        max_digits=12, decimal_places=2, default=5000,
        help_text="Amount requiring Treasurer+Admin approval"
    )
    committee_vote_threshold = models.DecimalField(
        max_digits=12, decimal_places=2, default=10000,
        help_text="Amount requiring committee vote"
    )
    
    # Required Attachments
    require_receipt_above_threshold = models.DecimalField(
        max_digits=12, decimal_places=2, default=1000,
        help_text="Receipt required above this amount"
    )
    
    # Categories (stored as JSON)
    expense_categories = models.JSONField(
        default=list,
        blank=True,
        help_text="List of allowed expense categories"
    )

    class Meta:
        verbose_name = "Expense Policy"
        verbose_name_plural = "Expense Policies"


class PaymentProviderConfig(BaseModel):
    """Payment provider configuration for a Chama."""
    chama = models.ForeignKey(
        Chama, on_delete=models.CASCADE, related_name="payment_configs"
    )
    
    # Provider Type
    provider_type = models.CharField(max_length=50)  # mpesa_stk, mpesa_paybill, bank, manual
    
    # M-Pesa Settings
    mpesa_shortcode = models.CharField(max_length=10, blank=True)
    mpesa_passkey = models.CharField(max_length=255, blank=True)
    mpesa_callback_url = models.URLField(blank=True)
    mpesa_account_reference_format = models.CharField(
        max_length=50,
        default="CHAMA_{{chama_id}}_{{member_id}}",
        help_text="Template for account reference"
    )
    
    # Bank Settings
    bank_name = models.CharField(max_length=100, blank=True)
    bank_account_number = models.CharField(max_length=50, blank=True)
    bank_branch = models.CharField(max_length=100, blank=True)
    
    # Manual/Cash
    allow_manual_entry = models.BooleanField(default=True)
    manual_entry_requires_approval = models.BooleanField(default=True)
    
    # Reconciliation
    auto_reconcile = models.BooleanField(default=True)
    
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Payment Provider Config"
        verbose_name_plural = "Payment Provider Configs"
