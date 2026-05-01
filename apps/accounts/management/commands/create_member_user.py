from __future__ import annotations

import secrets
import string

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.kyc.services import sync_user_access_state

User = get_user_model()


def _generate_password(length: int = 18) -> str:
    if length < 12:
        length = 12
    alphabet = string.ascii_letters + string.digits
    symbols = "!@#$%^&*()-_=+"

    # Ensure a reasonable mix for common password policies.
    required = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice(symbols),
    ]
    remaining = [secrets.choice(alphabet + symbols) for _ in range(length - len(required))]
    chars = required + remaining
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def _generate_unique_phone() -> str:
    # Keep within a Kenyan +2547XXXXXXXX pattern.
    # Use the +254700000000-+254700000999 range reserved for local dev.
    for _ in range(2000):
        candidate = f"+254700{secrets.randbelow(1000000):06d}"
        if not User.objects.filter(phone=candidate).exists():
            return candidate
    raise CommandError("Unable to generate a unique phone number; DB seems saturated.")


class Command(BaseCommand):
    help = "Create a verified member user (non-staff) and print credentials."

    def add_arguments(self, parser):
        parser.add_argument("--phone", type=str, default="", help="Phone number (Kenyan).")
        parser.add_argument("--password", type=str, default="", help="Password (optional).")
        parser.add_argument("--full-name", type=str, default="Member User", help="Full name.")
        parser.add_argument("--email", type=str, default="", help="Email (optional).")
        parser.add_argument(
            "--update-existing",
            action="store_true",
            help="If the phone exists, update password/verification flags.",
        )
        parser.add_argument(
            "--no-verify",
            action="store_true",
            help="Do not mark phone/OTP verified.",
        )

    def handle(self, *args, **options):
        raw_phone = str(options.get("phone") or "").strip()
        phone = raw_phone or _generate_unique_phone()
        password = str(options.get("password") or "").strip() or _generate_password()
        full_name = str(options.get("full_name") or "Member User").strip() or "Member User"
        email = str(options.get("email") or "").strip() or None
        update_existing = bool(options.get("update_existing"))
        verify = not bool(options.get("no_verify"))

        user = User.objects.filter(phone=phone).first()
        created = False

        if user and not update_existing:
            raise CommandError(
                f"User with phone {phone} already exists. Use --update-existing to modify it."
            )

        if not user:
            user = User.objects.create_user(
                phone=phone,
                password=password,
                full_name=full_name,
                email=email,
                is_active=True,
                is_staff=False,
                is_superuser=False,
            )
            created = True
        else:
            # Update existing.
            user.full_name = full_name
            user.email = email
            user.is_active = True
            user.is_staff = False
            user.is_superuser = False
            user.set_password(password)
            user.save(update_fields=["full_name", "email", "is_active", "is_staff", "is_superuser", "password"])

        if verify:
            now = timezone.now()
            user.phone_verified = True
            user.phone_verified_at = user.phone_verified_at or now
            if email:
                user.email_verified = True
                user.email_verified_at = user.email_verified_at or now
                user.save(
                    update_fields=[
                        "phone_verified",
                        "phone_verified_at",
                        "email_verified",
                        "email_verified_at",
                    ]
                )
            else:
                user.save(update_fields=["phone_verified", "phone_verified_at"])

            # Ensure derived flags (otp_verified, tier_access, kyc_status, etc) are consistent.
            user = sync_user_access_state(user)

        self.stdout.write(self.style.SUCCESS("✓ Member user ready"))
        self.stdout.write(f"created={created}")
        self.stdout.write(f"user_id={user.id}")
        self.stdout.write(f"phone={user.phone}")
        self.stdout.write(f"password={password}")
        self.stdout.write(f"phone_verified={user.phone_verified}")
        self.stdout.write(f"otp_verified={user.otp_verified}")
        self.stdout.write(f"email_verified={getattr(user, 'email_verified', False)}")
        self.stdout.write(f"tier_access={getattr(user, 'tier_access', '')}")
        self.stdout.write(f"kyc_status={getattr(user, 'kyc_status', '')}")

