#!/usr/bin/env python
"""
Script to list all users, their roles, and login credentials for testing.
Usage: python manage.py shell < get_test_users.py
"""

import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.chama.models import Membership, Chama

User = get_user_model()

# Fetch all users
users = User.objects.all().order_by('phone')

# Display table
print("\n" + "=" * 140)
print("TEST USERS - Roles and Login Credentials")
print("=" * 140)
print(f"{'Phone (Login)':<20} | {'Full Name':<25} | {'Active':<8} | {'Verified':<8} | {'Chama':<20} | {'Role':<15} | {'Status':<12}")
print("-" * 140)

for user in users:
    phone = user.phone
    full_name = user.full_name[:25] if user.full_name else "N/A"
    is_active = "Yes" if user.is_active else "No"
    phone_verified = "Yes" if user.phone_verified else "No"
    
    # Get memberships and roles
    memberships = Membership.objects.filter(user=user)
    
    if memberships.exists():
        for membership in memberships:
            chama_name = membership.chama.name[:20] if membership.chama.name else "N/A"
            role = membership.role
            status = membership.status
            print(f"{phone:<20} | {full_name:<25} | {is_active:<8} | {phone_verified:<8} | {chama_name:<20} | {role:<15} | {status:<12}")
    else:
        # User with no memberships
        print(f"{phone:<20} | {full_name:<25} | {is_active:<8} | {phone_verified:<8} | {'N/A':<20} | {'N/A':<15} | {'N/A':<12}")

print("=" * 140)

print("\n📝 LOGIN INSTRUCTIONS:")
print("-" * 140)
print("1. Use the 'Phone (Login)' column as the username/phone for authentication")
print("2. Authentication is phone-number based (no password required)")
print("3. OTP will be sent to the phone number provided")
print("4. Status codes:")
print("   - 'pending': User joined but not yet approved")
print("   - 'active': User is an active member")
print("   - 'suspended': User is suspended from the chama")
print("   - 'exited': User has exited the chama")
print("-" * 140)

print("\n🔐 ROLE DESCRIPTIONS:")
print("-" * 140)
roles_desc = {
    "SUPERADMIN": "Super Admin - Full system access",
    "ADMIN": "Admin - Chama administrative access",
    "CHAMA_ADMIN": "Chama Admin - Chama administration",
    "TREASURER": "Treasurer - Financial operations",
    "SECRETARY": "Secretary - Record management",
    "MEMBER": "Member - Regular member access",
    "AUDITOR": "Auditor - Financial audit access",
}
for role, desc in roles_desc.items():
    print(f"   • {role:15} - {desc}")
print("-" * 140)

# Summary statistics
print("\n📊 SUMMARY:")
print("-" * 140)
print(f"Total Users: {len(users)}")
active_users = users.filter(is_active=True).count()
verified_users = users.filter(phone_verified=True).count()
print(f"Active Users: {active_users}")
print(f"Verified Users: {verified_users}")

# Get membership statistics
all_memberships = Membership.objects.all()
print(f"Total Memberships: {all_memberships.count()}")
print(f"Chamas: {Chama.objects.count()}")

# Get role distribution
print("\nRole Distribution:")
role_counts = {}
for role_choice in ["SUPERADMIN", "ADMIN", "CHAMA_ADMIN", "TREASURER", "SECRETARY", "MEMBER", "AUDITOR"]:
    count = all_memberships.filter(role=role_choice).count()
    if count > 0:
        role_counts[role_choice] = count
        print(f"   • {role_choice:15} - {count} members")

print("\n" + "=" * 140)
print("RECOMMENDED TEST USER GROUPS FOR FEATURE TESTING:")
print("=" * 140)

# Find test users for different scenarios
treasurers = all_memberships.filter(role="TREASURER").select_related('user', 'chama')
admins = all_memberships.filter(role="ADMIN").select_related('user', 'chama')
members = all_memberships.filter(role="MEMBER").select_related('user', 'chama')

if treasurers.exists():
    print("\n💰 FOR TESTING FINANCIAL FEATURES (Transfers, Payments, Loan Updates):")
    print("-" * 140)
    for m in treasurers[:2]:
        print(f"   Phone: {m.user.phone:20} | Name: {m.user.full_name:25} | Role: {m.role:15} | Chama: {m.chama.name}")

if admins.exists():
    print("\n👨‍💼 FOR TESTING ADMIN FEATURES (Approvals, Loan Updates):")
    print("-" * 140)
    for m in admins[:2]:
        print(f"   Phone: {m.user.phone:20} | Name: {m.user.full_name:25} | Role: {m.role:15} | Chama: {m.chama.name}")

if members.exists():
    print("\n👤 FOR TESTING MEMBER FEATURES (Transfers, Payments):")
    print("-" * 140)
    for m in members[:3]:
        print(f"   Phone: {m.user.phone:20} | Name: {m.user.full_name:25} | Role: {m.role:15} | Chama: {m.chama.name}")

print("\n" + "=" * 140 + "\n")
