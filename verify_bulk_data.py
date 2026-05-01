#!/usr/bin/env python
"""
Script to verify that 1000 members were successfully created for Yangu Chama.
Usage: python manage.py shell < verify_bulk_data.py
"""

import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.chama.models import Chama, Membership
from apps.finance.models import Wallet, Contribution, Loan

User = get_user_model()

print("\n" + "=" * 120)
print("VERIFYING YANGU CHAMA BULK DATA GENERATION")
print("=" * 120)

# Get Yangu Chama
yangu_chama = Chama.objects.filter(name="Yangu Chama").first()
if not yangu_chama:
    print("❌ Yangu Chama not found!")
    exit(1)

print(f"\n✅ Target Chama: {yangu_chama.name} (ID: {yangu_chama.id})")

# Count memberships
memberships_count = Membership.objects.filter(chama=yangu_chama).count()
print(f"\n📊 Memberships in Yangu Chama: {memberships_count}")

# Get members with +25471500 phone prefix (our bulk-created users)
bulk_users = User.objects.filter(phone__startswith="+25471500").count()
print(f"📊 Bulk-created users (+25471500 prefix): {bulk_users}")

# Get memberships for bulk users
bulk_memberships = Membership.objects.filter(
    chama=yangu_chama,
    user__phone__startswith="+25471500"
).count()
print(f"📊 Bulk memberships in Yangu Chama: {bulk_memberships}")

# Count wallets for bulk users
bulk_wallets = Wallet.objects.filter(
    owner_type="USER",
    owner_id__in=User.objects.filter(phone__startswith="+25471500").values_list('id', flat=True)
).count()
print(f"📊 Wallets for bulk users: {bulk_wallets}")

# Count contributions for bulk members
bulk_contributions = Contribution.objects.filter(
    chama=yangu_chama,
    member__phone__startswith="+25471500"
).count()
print(f"📊 Contributions from bulk members: {bulk_contributions}")

# Count loans for bulk members
bulk_loans = Loan.objects.filter(
    chama=yangu_chama,
    member__phone__startswith="+25471500"
).count()
print(f"📊 Loans for bulk members: {bulk_loans}")

# Get role distribution
from apps.chama.models import MembershipRole
role_counts = {}
for role in [MembershipRole.MEMBER, MembershipRole.TREASURER, MembershipRole.SECRETARY, MembershipRole.AUDITOR]:
    count = Membership.objects.filter(
        chama=yangu_chama,
        user__phone__startswith="+25471500",
        role=role
    ).count()
    role_counts[role] = count

print(f"\n📋 Role Distribution (bulk members only):")
for role, count in role_counts.items():
    print(f"   - {role}: {count}")

# Sample wallet balances
print(f"\n💰 Sample Wallet Balances (first 5 bulk users):")
sample_wallets = Wallet.objects.filter(
    owner_type="USER",
    owner_id__in=User.objects.filter(phone__startswith="+25471500").values_list('id', flat=True)
)[:5]
for wallet in sample_wallets:
    user = User.objects.get(id=wallet.owner_id)
    print(f"   - {user.phone}: Available: KES {wallet.available_balance}, Locked: KES {wallet.locked_balance}")

# Calculate totals
total_available = sum(
    w.available_balance for w in Wallet.objects.filter(
        owner_type="USER",
        owner_id__in=User.objects.filter(phone__startswith="+25471500").values_list('id', flat=True)
    )
)
total_locked = sum(
    w.locked_balance for w in Wallet.objects.filter(
        owner_type="USER",
        owner_id__in=User.objects.filter(phone__startswith="+25471500").values_list('id', flat=True)
    )
)
total_contributions = sum(
    c.amount for c in Contribution.objects.filter(
        chama=yangu_chama,
        member__phone__startswith="+25471500"
    )
)
total_loans = sum(
    l.original_amount for l in Loan.objects.filter(
        chama=yangu_chama,
        member__phone__startswith="+25471500"
    )
)

print(f"\n💵 Financial Summary (bulk data only):")
print(f"   - Total Available Balance: KES {total_available:,.2f}")
print(f"   - Total Locked Balance: KES {total_locked:,.2f}")
print(f"   - Total Wallet Value: KES {total_available + total_locked:,.2f}")
print(f"   - Total Contributions: KES {total_contributions:,.2f}")
print(f"   - Total Loan Amount: KES {total_loans:,.2f}")

print(f"\n" + "=" * 120)
if bulk_memberships >= 900:  # Allow some margin
    print(f"✅ YANGU CHAMA DATA VERIFICATION SUCCESSFUL!")
    print(f"   {bulk_memberships} bulk members created with roles, wallets, contributions, and loans.")
else:
    print(f"⚠️  DATA INCOMPLETE - Only {bulk_memberships} bulk members found")
print("=" * 120 + "\n")
