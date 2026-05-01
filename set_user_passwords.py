#!/usr/bin/env python
"""
Script to set passwords for test users.
Usage: python manage.py shell < set_user_passwords.py
"""

import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()

print("\n" + "=" * 100)
print("SETTING PASSWORDS FOR TEST USERS")
print("=" * 100)

# Test users with their passwords
users_passwords = [
    {
        "phone": "+254700000010",
        "name": "Grace Chama Admin",
        "password": "Grace@2026Admin123"
    },
    {
        "phone": "+254700000011",
        "name": "Robert Treasurer",
        "password": "Robert@2026Treasurer123"
    },
    {
        "phone": "+254700000012",
        "name": "Patricia Secretary",
        "password": "Patricia@2026Secretary123"
    },
    {
        "phone": "+254700000013",
        "name": "Michael Auditor",
        "password": "Michael@2026Auditor123"
    },
    {
        "phone": "+254700000014",
        "name": "Lisa Member",
        "password": "Lisa@2026Member123"
    },
]

print("\n✅ SETTING PASSWORDS FOR TEST USERS:")
print("-" * 100)
print(f"{'Phone':<20} | {'Name':<25} | {'Password':<30}")
print("-" * 100)

for data in users_passwords:
    try:
        user = User.objects.get(phone=data["phone"])
        user.set_password(data["password"])
        user.save()
        print(f"{data['phone']:<20} | {data['name']:<25} | {data['password']:<30}")
    except User.DoesNotExist:
        print(f"❌ User not found: {data['phone']}")

# Also set passwords for existing demo users
existing_users = [
    {
        "phone": "+254700000001",
        "name": "System Admin",
        "password": "Admin@2026Demo123"
    },
    {
        "phone": "+254700000002",
        "name": "Jane Secretary",
        "password": "Jane@2026Demo123"
    },
    {
        "phone": "+254700000003",
        "name": "John Treasurer",
        "password": "John@2026Demo123"
    },
    {
        "phone": "+254700000004",
        "name": "Alice Member",
        "password": "Alice@2026Demo123"
    },
    {
        "phone": "+254700000005",
        "name": "Bob Member",
        "password": "Bob@2026Demo123"
    },
]

print("\n✅ SETTING PASSWORDS FOR EXISTING DEMO USERS:")
print("-" * 100)
print(f"{'Phone':<20} | {'Name':<25} | {'Password':<30}")
print("-" * 100)

for data in existing_users:
    try:
        user = User.objects.get(phone=data["phone"])
        user.set_password(data["password"])
        user.save()
        print(f"{data['phone']:<20} | {data['name']:<25} | {data['password']:<30}")
    except User.DoesNotExist:
        print(f"❌ User not found: {data['phone']}")

print("\n" + "=" * 100)
print("✅ PASSWORDS SET SUCCESSFULLY")
print("=" * 100)

print("\n📝 TEST CREDENTIALS (Phone + Password):")
print("-" * 100)
print("\nTest Chama Alpha (NEW):")
for data in users_passwords:
    print(f"  Phone: {data['phone']:<20} | Password: {data['password']:<30}")

print("\nDemo Chama (EXISTING):")
for data in existing_users:
    print(f"  Phone: {data['phone']:<20} | Password: {data['password']:<30}")

print("\n" + "=" * 100)
print("🔐 AUTHENTICATION OPTIONS:")
print("-" * 100)
print("1. OTP Authentication (Recommended - Phone-based):")
print("   • Use phone number only")
print("   • System sends OTP via SMS")
print("   • Enter OTP to authenticate")
print("\n2. Password Authentication (Alternative):")
print("   • Use phone number + password")
print("   • Use credentials listed above")
print("-" * 100 + "\n")
