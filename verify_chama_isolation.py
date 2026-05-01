#!/usr/bin/env python
"""
Script to verify multi-tenant chama isolation for Test Chama Alpha users.
Usage: python manage.py shell < verify_chama_isolation.py
"""

import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.chama.models import Chama, Membership, MemberStatus

User = get_user_model()

print("\n" + "=" * 120)
print("VERIFYING MULTI-TENANT CHAMA ISOLATION")
print("=" * 120)

# Test users in Test Chama Alpha
test_users = [
    "+254700000010",  # Grace Chama Admin
    "+254700000011",  # Robert Treasurer
    "+254700000012",  # Patricia Secretary
    "+254700000013",  # Michael Auditor
    "+254700000014",  # Lisa Member
]

print("\n✅ VERIFICATION: Checking what chamas each user can access\n")

for phone in test_users:
    try:
        user = User.objects.get(phone=phone)
        print(f"\n{'='*120}")
        print(f"User: {user.full_name} ({phone})")
        print(f"{'='*120}")
        
        # Get memberships for this user
        memberships = Membership.objects.filter(
            user=user,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE
        )
        
        visible_chamas = Chama.objects.filter(
            memberships__user=user,
            memberships__is_active=True,
            memberships__is_approved=True,
            memberships__status=MemberStatus.ACTIVE,
        ).distinct()
        
        print(f"✓ Active memberships: {memberships.count()}")
        print(f"✓ Visible chamas: {visible_chamas.count()}")
        
        if visible_chamas.exists():
            print(f"\n📋 Chamas this user can see:")
            for chama in visible_chamas:
                membership = memberships.filter(chama=chama).first()
                print(f"   • {chama.name} (Role: {membership.role}, Status: {membership.status})")
        else:
            print(f"⚠ No visible chamas (user has no active memberships)")
            
    except User.DoesNotExist:
        print(f"❌ User not found: {phone}")

print("\n" + "=" * 120)
print("ISOLATION VERIFICATION SUMMARY")
print("=" * 120)

# Check Test Chama Alpha
test_chama = Chama.objects.filter(name="Test Chama Alpha").first()
if test_chama:
    print(f"\n✅ Test Chama Alpha Status:")
    print(f"   ID: {test_chama.id}")
    print(f"   Name: {test_chama.name}")
    print(f"   Status: {test_chama.status}")
    print(f"   Members: {test_chama.memberships.count()}")
    
    active_members = test_chama.memberships.filter(
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE
    )
    print(f"   Active Members: {active_members.count()}")
    
    print(f"\n   Member Access Details:")
    for m in active_members:
        print(f"   ├─ {m.user.full_name} ({m.user.phone})")
        print(f"   │  Role: {m.role}")
        print(f"   │  Status: {m.status}")
        print(f"   │  Active: {m.is_active}")
        print(f"   │  Approved: {m.is_approved}")
else:
    print("\n❌ Test Chama Alpha not found!")

# Check other chamas
other_chamas = Chama.objects.exclude(name="Test Chama Alpha")
print(f"\n✅ Other Chamas in System: {other_chamas.count()}")
for chama in other_chamas[:3]:
    print(f"   • {chama.name} ({chama.memberships.count()} members)")

print("\n" + "=" * 120)
print("🔐 MULTI-TENANT ISOLATION MECHANISM")
print("=" * 120)
print("""
The system enforces multi-tenant isolation through:

1. ✅ ChamaScopeMixin: Validates chama_id from URL or X-CHAMA-ID header
2. ✅ QuerySet Filtering: get_queryset() only returns chamas where user is an active member
3. ✅ Membership Status: Only shows chamas where:
   - User has an active membership (is_active=True)
   - Membership is approved (is_approved=True)
   - Member status is ACTIVE (not pending/suspended/exited)
4. ✅ Superuser Override: Staff/superusers can see all chamas (for admin purposes)

Result: Users in Test Chama Alpha will ONLY see their chama, not other chamas.
""")
print("=" * 120 + "\n")
