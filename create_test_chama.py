#!/usr/bin/env python
"""
Script to generate a new Chama with 5 members in various roles.
Usage: python manage.py shell < create_test_chama.py
"""

import os
import django
from datetime import timedelta

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus

User = get_user_model()

print("\n" + "=" * 100)
print("CREATING NEW TEST CHAMA WITH 5 MEMBERS")
print("=" * 100)

# Create new Chama
chama_name = "Test Chama Alpha"
chama = Chama.objects.create(
    name=chama_name,
    description="Test chama for wallet feature validation",
    county="Nairobi",
    subcounty="Westlands",
    currency="KES",
    status="active",
    chama_type="savings",
    privacy="invite_only",
    join_enabled=True,
    require_approval=True,
    max_members=50
)
print(f"\n✅ Created Chama: {chama.name}")
print(f"   ID: {chama.id}")
print(f"   Join Code: {chama.join_code}")

# Define members with roles
members_data = [
    {
        "phone": "+254700000010",
        "name": "Grace Chama Admin",
        "role": MembershipRole.CHAMA_ADMIN,
    },
    {
        "phone": "+254700000011",
        "name": "Robert Treasurer",
        "role": MembershipRole.TREASURER,
    },
    {
        "phone": "+254700000012",
        "name": "Patricia Secretary",
        "role": MembershipRole.SECRETARY,
    },
    {
        "phone": "+254700000013",
        "name": "Michael Auditor",
        "role": MembershipRole.AUDITOR,
    },
    {
        "phone": "+254700000014",
        "name": "Lisa Member",
        "role": MembershipRole.MEMBER,
    },
]

print("\n" + "-" * 100)
print("CREATING 5 MEMBERS WITH SPECIFIED ROLES:")
print("-" * 100)

created_members = []
for data in members_data:
    # Create or get user
    user, created = User.objects.get_or_create(
        phone=data["phone"],
        defaults={
            "full_name": data["name"],
            "is_active": True,
            "phone_verified": True,
            "phone_verified_at": timezone.now(),
        }
    )
    
    if created:
        print(f"✅ Created User: {user.full_name} ({user.phone})")
    else:
        print(f"✓ User exists: {user.full_name} ({user.phone})")
    
    # Create membership
    membership, m_created = Membership.objects.get_or_create(
        user=user,
        chama=chama,
        defaults={
            "role": data["role"],
            "status": MemberStatus.ACTIVE,
            "is_active": True,
            "is_approved": True,
            "joined_at": timezone.now(),
            "approved_at": timezone.now(),
        }
    )
    
    if m_created:
        print(f"   └─ Role: {data['role']} (NEW MEMBERSHIP)")
    else:
        print(f"   └─ Role: {data['role']} (EXISTING)")
    
    created_members.append({
        "user": user,
        "membership": membership,
        "role": data["role"]
    })

print("\n" + "=" * 100)
print("TEST CHAMA CREATED SUCCESSFULLY")
print("=" * 100)

print(f"\n📋 CHAMA DETAILS:")
print(f"   Chama Name: {chama.name}")
print(f"   Chama ID: {chama.id}")
print(f"   Join Code: {chama.join_code}")
print(f"   Status: {chama.status}")
print(f"   Members: {chama.memberships.count()}")

print(f"\n👥 MEMBER CREDENTIALS (Use these for testing):")
print("-" * 100)
print(f"{'Phone':<20} | {'Name':<25} | {'Role':<15}")
print("-" * 100)
for member in created_members:
    print(f"{member['user'].phone:<20} | {member['user'].full_name:<25} | {member['role']:<15}")

print("\n" + "=" * 100)
print("🔐 LOGIN INSTRUCTIONS:")
print("-" * 100)
print("1. Use any phone number above to login")
print("2. System will send OTP to the phone")
print("3. Enter OTP to authenticate")
print("4. User will have access to the new chama with their assigned role")
print("-" * 100)

print("\n✅ Test Chama is ready for wallet feature testing!")
print("=" * 100 + "\n")
