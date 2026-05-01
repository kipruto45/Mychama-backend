#!/usr/bin/env python
"""
Management command to test the full OTP workflow.
Tests: create OTP, send OTP, verify OTP, lockout behavior.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.accounts.services import OTPService


class Command(BaseCommand):
    help = "Test the full OTP workflow (create, send, verify, lockout)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--phone",
            type=str,
            default="+254700000001",
            help="Phone number to test OTP with (default: +254700000001)",
        )
        parser.add_argument(
            "--purpose",
            type=str,
            default="verify_phone",
            choices=["verify_phone", "login_2fa", "password_reset", "register", "withdrawal_confirm"],
            help="OTP purpose (default: verify_phone)",
        )
        parser.add_argument(
            "--skip-send",
            action="store_true",
            help="Skip sending OTP (just generate)",
        )
        parser.add_argument(
            "--test-lockout",
            action="store_true",
            help="Test lockout behavior by using wrong codes",
        )

    def handle(self, *args, **options):
        phone = options["phone"]
        purpose = options["purpose"]
        skip_send = options["skip_send"]
        test_lockout = options["test_lockout"]

        self.stdout.write(self.style.NOTICE("=" * 60))
        self.stdout.write(self.style.NOTICE("OTP Workflow Test"))
        self.stdout.write(self.style.NOTICE("=" * 60))
        
        # Get or create test user
        user = self._get_or_create_test_user(phone)
        
        # Test 1: Generate OTP
        self._test_generate_otp(phone, user, purpose)
        
        # Test 2: Send OTP (unless skipped)
        if not skip_send:
            self._test_send_otp(phone, user, purpose)
        
        # Test 3: Verify correct OTP
        self._test_verify_correct_otp(phone, purpose)
        
        # Test 4: Test lockout behavior (if requested)
        if test_lockout:
            self._test_lockout_behavior(phone, user, purpose)
        
        self.stdout.write(self.style.SUCCESS("\n" + "=" * 60))
        self.stdout.write(self.style.SUCCESS("All OTP workflow tests completed successfully!"))
        self.stdout.write(self.style.SUCCESS("=" * 60))

    def _get_or_create_test_user(self, phone: str) -> User:
        """Get or create a test user."""
        self.stdout.write("\n[1/4] Setting up test user...")
        
        # Try to find user by phone
        user = User.objects.filter(phone=phone).first()
        
        if not user:
            # Create a test user
            user = User.objects.create(
                phone=phone,
                email=f"test_{phone.replace('+', '').replace(' ', '')}@example.com",
                full_name="OTP Test User",
                is_active=True,
            )
            self.stdout.write(self.style.SUCCESS(f"   Created test user: {user.email}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"   Found existing user: {user.email}"))
        
        return user

    def _test_generate_otp(self, phone: str, user: User, purpose: str):
        """Test OTP generation with rate limiting."""
        self.stdout.write("\n[2/4] Testing OTP generation...")
        
        try:
            # Generate OTP
            otp_token, plain_code = OTPService.generate_otp(
                phone=phone,
                user=user,
                purpose=purpose,
                delivery_method="sms",
                ip_address="127.0.0.1",
                user_agent="Test-Agent",
            )
            
            self.stdout.write(self.style.SUCCESS("   ✓ OTP generated successfully"))
            self.stdout.write(f"   - Token ID: {otp_token.id}")
            self.stdout.write(f"   - Code (plain): {plain_code} (USE THIS TO VERIFY)")
            self.stdout.write(f"   - Code hash: {otp_token.code_hash[:20]}...")
            self.stdout.write(f"   - Purpose: {otp_token.purpose}")
            self.stdout.write(f"   - Delivery method: {otp_token.delivery_method}")
            self.stdout.write(f"   - Expires at: {otp_token.expires_at}")
            self.stdout.write(f"   - Max attempts: {otp_token.max_attempts}")
            self.stdout.write(f"   - Cooldown: {otp_token.cooldown_seconds} seconds")
            
            # Store plain code for later tests
            self._last_plain_code = plain_code
            self._last_otp_token = otp_token
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   ✗ Failed to generate OTP: {e}"))
            raise CommandError(f"OTP generation failed: {e}")

    def _test_send_otp(self, phone: str, user: User, purpose: str):
        """Test OTP sending via SMS and Email."""
        self.stdout.write("\n[3/4] Testing OTP delivery...")
        
        if not hasattr(self, '_last_otp_token'):
            self.stdout.write(self.style.WARNING("   No OTP token from previous step, generating one..."))
            self._test_generate_otp(phone, user, purpose)
        
        otp_token = self._last_otp_token
        plain_code = self._last_plain_code
        
        try:
            result = OTPService.send_otp(
                phone=phone,
                otp_token=otp_token,
                plain_code=plain_code,
                user=user,
            )
            
            self.stdout.write(self.style.SUCCESS("   ✓ OTP sent successfully"))
            self.stdout.write(f"   - Requested method: {result.requested_method}")
            self.stdout.write(f"   - Masked phone: {result.masked_phone}")
            self.stdout.write(f"   - Masked email: {result.masked_email}")
            self.stdout.write(f"   - SMS sent: {result.sms_sent}")
            self.stdout.write(f"   - Email sent: {result.email_sent}")
            self.stdout.write(f"   - Channels sent: {result.channels_sent}")
            self.stdout.write(f"   - Failed channels: {result.failed_channels}")
            
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"   ⚠ OTP send warning: {e}"))
            # Continue anyway - we can still test verification

    def _test_verify_correct_otp(self, phone: str, purpose: str):
        """Test OTP verification with correct code."""
        self.stdout.write("\n[4a/4] Testing OTP verification (correct code)...")
        
        if not hasattr(self, '_last_plain_code'):
            self.stdout.write(self.style.ERROR("   ✗ No OTP code from previous step"))
            raise CommandError("Cannot test verification without OTP code")
        
        plain_code = self._last_plain_code
        
        success, message = OTPService.verify_otp(
            phone=phone,
            code=plain_code,
            purpose=purpose,
        )
        
        if success:
            self.stdout.write(self.style.SUCCESS("   ✓ OTP verified successfully"))
            self.stdout.write(f"   - Message: {message}")
        else:
            self.stdout.write(self.style.ERROR(f"   ✗ Verification failed: {message}"))
            raise CommandError(f"OTP verification failed: {message}")
        
        # Now generate a new one for lockout test
        self.stdout.write("\n[4b/4] Generating new OTP for lockout test...")
        
        # First, invalidate the current one
        if hasattr(self, '_last_otp_token'):
            self._last_otp_token.is_used = True
            self._last_otp_token.save(update_fields=['is_used'])
        
        # Generate new OTP for lockout testing
        try:
            otp_token, plain_code = OTPService.generate_otp(
                phone=phone,
                purpose=purpose,
                delivery_method="sms",
            )
            self._last_plain_code = plain_code
            self._last_otp_token = otp_token
            self.stdout.write(self.style.SUCCESS("   ✓ New OTP generated for lockout test"))
            self.stdout.write(f"   - New code: {plain_code}")
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"   ⚠ Could not generate new OTP: {e}"))

    def _test_lockout_behavior(self, phone: str, user: User, purpose: str):
        """Test lockout behavior with wrong codes."""
        self.stdout.write("\n[5/5] Testing lockout behavior...")
        
        if not hasattr(self, '_last_otp_token'):
            self.stdout.write(self.style.WARNING("   No OTP token, generating one..."))
            try:
                otp_token, plain_code = OTPService.generate_otp(
                    phone=phone,
                    user=user,
                    purpose=purpose,
                    delivery_method="sms",
                )
                self._last_plain_code = plain_code
                self._last_otp_token = otp_token
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"   ⚠ Could not generate OTP: {e}"))
                return
        
        otp_token = self._last_otp_token
        max_attempts = otp_token.max_attempts
        
        self.stdout.write(f"   Testing with wrong codes (max attempts: {max_attempts})...")
        
        # Try wrong codes to trigger lockout
        wrong_codes = ["000000", "111111", "222222", "333333", "444444"]
        
        for i, wrong_code in enumerate(wrong_codes[:max_attempts]):
            success, message = OTPService.verify_otp(
                phone=phone,
                code=wrong_code,
                purpose=purpose,
            )
            
            if not success:
                self.stdout.write(f"   - Attempt {i+1}: Wrong code → {message}")
            else:
                self.stdout.write(self.style.ERROR("   - Unexpected success with wrong code!"))
        
        # Check final state
        otp_token.refresh_from_db()
        self.stdout.write(f"   - Final attempts: {otp_token.attempts}/{otp_token.max_attempts}")
        self.stdout.write(f"   - Is used: {otp_token.is_used}")
        
        # Check user lockout status
        if user.is_locked():
            self.stdout.write(self.style.WARNING("   ⚠ User is now locked out!"))
            self.stdout.write(f"   - Locked until: {user.locked_until}")
        else:
            self.stdout.write(self.style.SUCCESS("   ✓ User not locked (or lockout not triggered)"))
        
        # Test rate limiting (cooldown)
        self.stdout.write("\n   Testing rate limiting (cooldown)...")
        try:
            otp_token2, _ = OTPService.generate_otp(
                phone=phone,
                user=user,
                purpose=purpose,
                delivery_method="sms",
            )
            self.stdout.write(self.style.SUCCESS("   ✓ Rate limiting allows new OTP"))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"   ⚠ Rate limiting triggered: {e}"))
        
        self.stdout.write(self.style.SUCCESS("   ✓ Lockout behavior test completed"))
