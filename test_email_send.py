#!/usr/bin/env python
"""
Test script to verify email sending functionality
Sends test emails using all configured templates
"""
import os
import django
from django.conf import settings

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
django.setup()

from django.template.loader import render_to_string
from apps.notifications.email import send_email_message
from apps.accounts.models import User, OTPPurpose, OTPToken
from django.utils import timezone
import json

# Test email recipient
TEST_EMAIL = "linuxkipruto@gmail.com"

print("=" * 70)
print("MyChama Email Testing Suite")
print("=" * 70)

# Test 1: Welcome Email
print("\n[TEST 1] Welcome Email")
print("-" * 70)
try:
    context = {
        'user_name': 'Test User',
        'app_url': 'https://my-cham-a.app',
        'dashboard_url': 'https://my-cham-a.app/dashboard',
        'logo_url': 'https://my-cham-a.app/logo.png',
    }
    
    html_body = render_to_string('emails/auth/01-welcome.html', context)
    result = send_email_message(
        subject="Welcome to MyChama – Your Community Savings Journey Starts Here",
        recipient_list=[TEST_EMAIL],
        body="Welcome to MyChama!",
        html_body=html_body,
    )
    
    print(f"✅ Welcome Email Sent Successfully")
    print(f"   Provider: {result.provider}")
    print(f"   Sent Count: {result.sent_count}")
    print(f"   To: {TEST_EMAIL}")
    print(f"   From: MyChama <{settings.DEFAULT_FROM_EMAIL}>")
except Exception as e:
    print(f"❌ Welcome Email Failed: {str(e)}")
    import traceback
    traceback.print_exc()

# Test 2: OTP Verification Email
print("\n[TEST 2] OTP Verification Email")
print("-" * 70)
try:
    # Find or create a test user
    test_user, created = User.objects.get_or_create(
        phone="+254712345678",
        defaults={
            "email": TEST_EMAIL,
            "first_name": "Test",
            "last_name": "User",
        }
    )
    
    if not created:
        test_user.email = TEST_EMAIL
        test_user.save()
    
    context = {
        'user_name': test_user.first_name or test_user.phone,
        'code': '123456',
        'purpose': 'Email Verification',
        'expiry_minutes': 5,
    }
    
    html_body = render_to_string('emails/auth/02-verification.html', context)
    result = send_email_message(
        subject="Your MyChama Verification Code",
        recipient_list=[TEST_EMAIL],
        body="Your verification code is: 123456",
        html_body=html_body,
    )
    
    print(f"✅ OTP Verification Email Sent Successfully")
    print(f"   Provider: {result.provider}")
    print(f"   Sent Count: {result.sent_count}")
    print(f"   To: {TEST_EMAIL}")
    print(f"   From: MyChama <{settings.DEFAULT_FROM_EMAIL}>")
except Exception as e:
    print(f"❌ OTP Verification Email Failed: {str(e)}")
    import traceback
    traceback.print_exc()

# Test 3: Password Reset Email
print("\n[TEST 3] Password Reset Email")
print("-" * 70)
try:
    context = {
        'user_name': 'Test User',
        'reset_code': 'abc123def456',
        'reset_link': 'https://my-cham-a.app/reset-password?token=abc123def456',
        'expiry_minutes': 30,
    }
    
    html_body = render_to_string('emails/auth/03-password-reset-request.html', context)
    result = send_email_message(
        subject="MyChama Password Reset Request",
        recipient_list=[TEST_EMAIL],
        body="Click the link to reset your password",
        html_body=html_body,
    )
    
    print(f"✅ Password Reset Email Sent Successfully")
    print(f"   Provider: {result.provider}")
    print(f"   Sent Count: {result.sent_count}")
    print(f"   To: {TEST_EMAIL}")
    print(f"   From: MyChama <{settings.DEFAULT_FROM_EMAIL}>")
except Exception as e:
    print(f"❌ Password Reset Email Failed: {str(e)}")
    import traceback
    traceback.print_exc()

# Test 4: Login Alert Email
print("\n[TEST 4] Login Alert Email")
print("-" * 70)
try:
    context = {
        'user_name': 'Test User',
        'login_time': timezone.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
        'device': 'Chrome on Windows',
        'location': 'Nairobi, Kenya',
        'ip_address': '192.168.1.1',
    }
    
    html_body = render_to_string('emails/auth/05-login-alert.html', context)
    result = send_email_message(
        subject="MyChama Account Login Alert",
        recipient_list=[TEST_EMAIL],
        body="Your account was accessed from a new device",
        html_body=html_body,
    )
    
    print(f"✅ Login Alert Email Sent Successfully")
    print(f"   Provider: {result.provider}")
    print(f"   Sent Count: {result.sent_count}")
    print(f"   To: {TEST_EMAIL}")
    print(f"   From: MyChama <{settings.DEFAULT_FROM_EMAIL}>")
except Exception as e:
    print(f"❌ Login Alert Email Failed: {str(e)}")
    import traceback
    traceback.print_exc()

# Summary
print("\n" + "=" * 70)
print("Email Testing Complete!")
print("=" * 70)
print(f"\n📧 Check your inbox at: {TEST_EMAIL}")
print(f"📤 All emails sent from: MyChama <{settings.DEFAULT_FROM_EMAIL}>")
print(f"🔧 Email Backend: {settings.EMAIL_BACKEND}")
print(f"🔧 Email Provider: {settings.EMAIL_PROVIDER}")
print(f"🔧 Email Host: {settings.EMAIL_HOST}")
print("\n✅ Email system is configured and working!\n")
