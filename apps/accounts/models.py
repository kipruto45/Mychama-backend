import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from django.conf import settings

from apps.accounts.managers import UserManager
from core.utils import normalize_kenyan_phone


class OTPPurpose(models.TextChoices):
    VERIFY_PHONE = "verify_phone", "Phone Verification"
    LOGIN_2FA = "login_2fa", "Login 2FA"
    PASSWORD_RESET = "password_reset", "Password Reset"
    REGISTER = "register", "Registration"
    WITHDRAWAL_CONFIRM = "withdrawal_confirm", "Withdrawal Confirmation"


class OTPDeliveryMethod(models.TextChoices):
    SMS = "sms", "SMS"
    EMAIL = "email", "Email"
    BOTH = "both", "SMS + Email"


class OTPDeliveryChannel(models.TextChoices):
    SMS = "sms", "SMS"
    EMAIL = "email", "Email"


class OTPDeliveryStatus(models.TextChoices):
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    DELIVERED = "delivered", "Delivered"


class AuditCategory(models.TextChoices):
    AUTHENTICATION = "authentication", "Authentication"
    MEMBERSHIP = "membership", "Membership"
    FINANCE = "finance", "Finance"
    SECURITY = "security", "Security"
    ADMIN = "admin", "Administrative"


def _generate_otp_code() -> str:
    """Generate a 6-digit OTP code."""
    return str(secrets.randbelow(999999)).zfill(6)


class User(AbstractBaseUser, PermissionsMixin):
    """Primary user model - phone is the identity."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone = models.CharField(max_length=16, unique=True)
    email = models.EmailField(blank=True, null=True)
    full_name = models.CharField(max_length=255)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    referral_code = models.CharField(max_length=16, unique=True, null=True, blank=True)

    # Phone Verification (CRITICAL for chama approval)
    phone_verified = models.BooleanField(default=False)
    phone_verified_at = models.DateTimeField(null=True, blank=True)

    # Account Status
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    # Login Tracking
    last_login_at = models.DateTimeField(blank=True, null=True)
    last_login_ip = models.GenericIPAddressField(blank=True, null=True)
    date_joined = models.DateTimeField(auto_now_add=True)

    # 2FA Settings
    two_factor_enabled = models.BooleanField(default=False)
    two_factor_method = models.CharField(max_length=32, blank=True)
    two_factor_secret = models.CharField(max_length=255, blank=True)

    # Password Policy
    password_changed_at = models.DateTimeField(auto_now=True, blank=True, null=True)
    failed_login_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        ordering = ["-date_joined"]
        indexes = [
            models.Index(fields=["phone"]),
            models.Index(fields=["email"]),
            models.Index(fields=["phone_verified"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.full_name or self.phone

    def get_full_name(self):
        return self.full_name or self.phone

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.phone

    def _build_unique_referral_code(self):
        while True:
            code = f"REF{secrets.token_urlsafe(6)[:8].upper()}"
            if not User.objects.filter(referral_code=code).exclude(pk=self.pk).exists():
                return code

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        generated_referral_code = False

        self.phone = normalize_kenyan_phone(self.phone)
        if not self.referral_code:
            self.referral_code = self._build_unique_referral_code()
            generated_referral_code = True

        if update_fields is not None and generated_referral_code:
            kwargs["update_fields"] = set(update_fields) | {"referral_code"}

        super().save(*args, **kwargs)

    def is_locked(self) -> bool:
        """Check if account is currently locked."""
        if self.locked_until and self.locked_until > timezone.now():
            return True
        return False

    def check_password_history(self, password: str) -> bool:
        """Check if password was used before (prevent reuse)."""
        recent_passwords = self.password_history.filter(
            created_at__gte=timezone.now() - timedelta(days=90)
        ).order_by("-created_at")[:5]

        from django.contrib.auth.hashers import check_password
        for history in recent_passwords:
            if check_password(password, history.password_hash):
                return True
        return False

    def record_password_change(self, new_password: str):
        """Record password change to history."""
        from django.contrib.auth.hashers import make_password
        PasswordHistory.objects.create(
            user=self,
            password_hash=make_password(new_password)
        )
        self.password_changed_at = timezone.now()
        self.save(update_fields=["password_changed_at"])


class LoginEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="login_events",
    )
    identifier_attempted = models.CharField(max_length=255)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    success = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    # Device/session metadata for forensic analysis.
    device_id = models.CharField(max_length=128, blank=True)
    session_key = models.CharField(max_length=128, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["success", "created_at"]),
            models.Index(fields=["identifier_attempted", "created_at"]),
        ]

    def __str__(self):
        status = "success" if self.success else "failure"
        return f"{self.identifier_attempted} ({status}) @ {self.created_at:%Y-%m-%d %H:%M:%S}"


def _default_password_reset_expiry():
    from django.conf import settings
    from django.utils import timezone
    from datetime import timedelta
    expiry_minutes = getattr(settings, 'PASSWORD_RESET_TOKEN_MINUTES', 60)
    return timezone.now() + timedelta(minutes=expiry_minutes)


class PasswordResetToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="password_reset_tokens"
    )
    token_hash = models.CharField(max_length=128, db_index=True)
    expires_at = models.DateTimeField(default=_default_password_reset_expiry)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["used_at"]),
        ]

    def __str__(self):
        return f"Password reset token for {self.user.phone}"

    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_usable(self) -> bool:
        return self.used_at is None and not self.is_expired

    def mark_used(self):
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])


class MemberKYCStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


def _default_otp_expiry():
    from django.conf import settings
    from django.utils import timezone
    from datetime import timedelta
    expiry_minutes = getattr(settings, 'OTP_EXPIRY_MINUTES', 5)
    return timezone.now() + timedelta(minutes=expiry_minutes)


class OTPToken(models.Model):
    """
    One-Time Password token for phone verification and 2FA.
    Generates a 6-digit code valid for a short period (default 5 minutes).
    Stores code as hash for security.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Phone number (can be before user creation or for existing user)
    phone = models.CharField(max_length=16, db_index=True, blank=True, default='')
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="otp_tokens",
        null=True,
        blank=True,
    )
    
    # OTP code (stored as hash for security)
    code_hash = models.CharField(max_length=64, unique=True, blank=True, default='')  # SHA256 hash
    # Kept for backward compatibility with existing schema; new OTPs are not
    # persisted in plaintext and this field is cleared after use/retirement.
    code = models.CharField(max_length=6, blank=True, default='')
    
    # OTP Purpose
    purpose = models.CharField(
        max_length=20,
        choices=OTPPurpose.choices,
        default=OTPPurpose.VERIFY_PHONE,
    )
    
    delivery_method = models.CharField(
        max_length=20,
        choices=OTPDeliveryMethod.choices,
        default=OTPDeliveryMethod.SMS,
    )
    
    # Status
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=_default_otp_expiry)
    verified_at = models.DateTimeField(null=True, blank=True)
    
    # Rate limiting
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    
    # Cooldown between OTP requests (seconds)
    cooldown_seconds = models.PositiveIntegerField(default=60)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    sent_count = models.PositiveIntegerField(default=0)
    
    # IP tracking for security
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["phone", "purpose", "is_used"]),
            models.Index(fields=["code_hash", "purpose"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["phone", "created_at"]),
        ]

    def __str__(self):
        return f"OTP for {self.phone} ({self.purpose})"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_valid(self) -> bool:
        return not self.is_used and not self.is_expired and self.attempts < self.max_attempts
    
    @property
    def can_resend(self) -> bool:
        """Check if enough time has passed since last OTP was sent"""
        if not self.last_sent_at:
            return True
        elapsed = (timezone.now() - self.last_sent_at).total_seconds()
        return elapsed >= self.cooldown_seconds

    def verify(self, code: str) -> bool:
        """Verify OTP code and mark as used if valid."""
        # Rebuild the salted hash used at generation time.
        provided_hash = self.hash_code(code, self.phone, self.purpose)
        
        if not self.is_valid or provided_hash != self.code_hash:
            self.attempts += 1
            self.last_attempt_at = timezone.now()
            update_fields = ["attempts", "last_attempt_at"]
            if self.attempts >= self.max_attempts:
                self.is_used = True
                self.code = ""
                update_fields.extend(["is_used", "code"])
            self.save(update_fields=update_fields)
            return False
        
        self.is_used = True
        self.code = ""
        self.verified_at = timezone.now()
        self.save(update_fields=["is_used", "code", "verified_at"])
        return True
    
    @staticmethod
    def hash_code(code: str, phone: str, purpose: str) -> str:
        """Generate hash for OTP code with phone and purpose salt"""
        salt = f"{phone}:{purpose}"
        return hashlib.sha256(f"{code}:{salt}".encode()).hexdigest()


class OTPDeliveryLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    otp_token = models.ForeignKey(
        "accounts.OTPToken",
        on_delete=models.CASCADE,
        related_name="delivery_logs",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="otp_delivery_logs",
    )
    channel = models.CharField(max_length=10, choices=OTPDeliveryChannel.choices)
    provider_name = models.CharField(max_length=50, blank=True)
    provider_message_id = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=20, choices=OTPDeliveryStatus.choices)
    destination = models.CharField(max_length=255, blank=True)
    attempt_number = models.PositiveIntegerField(default=1)
    error_message = models.TextField(blank=True)
    provider_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["otp_token", "channel", "created_at"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        return f"{self.channel}:{self.status}:{self.otp_token_id}"


class UserPreference(models.Model):
    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="preferences",
    )
    active_chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="active_user_preferences",
    )
    low_data_mode = models.BooleanField(default=False)
    ussd_enabled = models.BooleanField(default=True)
    quiet_hours_start = models.TimeField(null=True, blank=True)
    quiet_hours_end = models.TimeField(null=True, blank=True)
    prefer_sms = models.BooleanField(default=True)
    prefer_email = models.BooleanField(default=True)
    prefer_in_app = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["active_chama"]),
            models.Index(fields=["low_data_mode"]),
        ]

    def __str__(self):
        return f"Preferences for {self.user_id}"


class MemberKYC(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="kyc_records",
    )
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="kyc_records",
    )
    id_number = models.CharField(max_length=32)
    id_front_image = models.FileField(upload_to="kyc/id_front/", blank=True)
    selfie_image = models.FileField(upload_to="kyc/selfie/", blank=True)
    status = models.CharField(
        max_length=20,
        choices=MemberKYCStatus.choices,
        default=MemberKYCStatus.PENDING,
    )
    review_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_kyc_records",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "chama"],
                name="uniq_member_kyc_per_user_chama",
            )
        ]
        indexes = [
            models.Index(fields=["chama", "status"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        return f"KYC {self.user_id} @ {self.chama_id} ({self.status})"


class MemberCard(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="member_cards",
    )
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="member_cards",
    )
    card_number = models.CharField(max_length=32)
    qr_token = models.CharField(max_length=64, unique=True, db_index=True)
    is_active = models.BooleanField(default=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-issued_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "chama"],
                condition=models.Q(is_active=True),
                name="uniq_active_member_card_per_user_chama",
            )
        ]
        indexes = [
            models.Index(fields=["chama", "is_active"]),
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["card_number"]),
        ]

    def __str__(self):
        return f"{self.card_number} ({self.user_id})"


class PasswordHistory(models.Model):
    """
    Track password history to prevent reuse.
    Stores hashed passwords for the last N passwords per user.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="password_history",
    )
    password_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
        ]

        def __str__(self):
            return f"Password history for {self.user.phone} at {self.created_at}"


class ReferralReward(models.Model):
    TRIAL_EXTENSION = "trial_extension"
    BILLING_CREDIT = "billing_credit"

    PENDING = "pending"
    APPLIED = "applied"
    SKIPPED = "skipped"

    REWARD_TYPE_CHOICES = [
        (TRIAL_EXTENSION, "Trial Extension"),
        (BILLING_CREDIT, "Billing Credit"),
    ]

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (APPLIED, "Applied"),
        (SKIPPED, "Skipped"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    referrer = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="referral_rewards",
    )
    referred_chama = models.OneToOneField(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="referral_reward",
    )
    rewarded_chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_referral_rewards",
    )
    reward_type = models.CharField(
        max_length=32,
        choices=REWARD_TYPE_CHOICES,
        default=TRIAL_EXTENSION,
    )
    reward_value = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING,
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["referrer", "status"]),
            models.Index(fields=["rewarded_chama", "status"]),
        ]

    def __str__(self):
        return f"{self.referrer_id} -> {self.referred_chama_id} ({self.status})"
