#!/usr/bin/env python
"""
Script to generate 1000 members for Yangu Chama with wallets, loans, and contributions.
Usage: python manage.py shell < generate_bulk_chama_members.py
"""

import os
import django
import random
from decimal import Decimal
from datetime import timedelta

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db import models
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.finance.models import Wallet, LedgerEntry, JournalEntry, Contribution, Loan, LoanStatus
from apps.accounts.models import User as AuthUser

User = get_user_model()

print("\n" + "=" * 120)
print("GENERATING 1000 MEMBERS FOR YANGU CHAMA")
print("=" * 120)

# Get Yangu Chama
yangu_chama = Chama.objects.filter(name="Yangu Chama").first()
if not yangu_chama:
    print("❌ Yangu Chama not found!")
    exit(1)

print(f"\n✅ Target Chama: {yangu_chama.name}")
print(f"   Current Members: {yangu_chama.memberships.count()}")

# Generate phone numbers (start from 01500000000 to avoid conflicts)
print("\n📱 Generating 1000 phone numbers and users...")
base_phone = 254715000000
users_to_create = []
used_phones = set(User.objects.values_list('phone', flat=True))

# Roles distribution for variety
role_distribution = [
    (MembershipRole.MEMBER, 950),      # 950 members (95%)
    (MembershipRole.TREASURER, 25),    # 25 treasurers
    (MembershipRole.SECRETARY, 15),    # 15 secretaries
    (MembershipRole.AUDITOR, 10),      # 10 auditors
]

member_count = 0
memberships_to_create = []
wallets_to_create = []
journal_entries_to_create = []
ledger_entries_to_create = []
contributions_to_create = []
loans_to_create = []

print(f"   Generating users batch...")
for i in range(1000):
    phone = f"+{base_phone + i}"
    
    # Skip if phone already exists
    if phone in used_phones:
        continue
    
    full_name = f"Yangu Member {i+1:04d}"
    
    # Create user
    user = User(
        phone=phone,
        full_name=full_name,
        is_active=True,
        phone_verified=True,
        phone_verified_at=timezone.now(),
    )
    users_to_create.append(user)

print(f"✅ Creating {len(users_to_create)} users in bulk...")
User.objects.bulk_create(users_to_create, batch_size=100)

# Reload to get the created users
created_users = User.objects.filter(phone__startswith="+25471500").order_by('phone')[:1000]
print(f"✅ Created {len(created_users)} users")

# Determine role distribution
total_users = len(created_users)
role_counts = {MembershipRole.MEMBER: 0, MembershipRole.TREASURER: 0, MembershipRole.SECRETARY: 0, MembershipRole.AUDITOR: 0}

# Shuffle users for random role assignment
users_list = list(created_users)
random.shuffle(users_list)

# Assign roles based on distribution
idx = 0
for role, count in role_distribution:
    for _ in range(min(count, total_users - idx)):
        user = users_list[idx]
        membership = Membership(
            user=user,
            chama=yangu_chama,
            role=role,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now() - timedelta(days=random.randint(1, 90)),
            approved_at=timezone.now() - timedelta(days=random.randint(1, 90)),
        )
        memberships_to_create.append(membership)
        role_counts[role] += 1
        idx += 1

print(f"\n✅ Creating {len(memberships_to_create)} memberships in bulk...")
Membership.objects.bulk_create(memberships_to_create, batch_size=100)

# Create wallets and ledger entries for each user
print(f"✅ Creating wallets for {len(created_users)} users...")

for user in created_users:
    # Create wallet
    wallet = Wallet(
        owner_type="USER",
        owner_id=user.id,
        available_balance=Decimal(random.uniform(1000, 50000)).quantize(Decimal('0.01')),
        locked_balance=Decimal(random.uniform(0, 5000)).quantize(Decimal('0.01')),
        currency="KES"
    )
    wallets_to_create.append(wallet)

Wallet.objects.bulk_create(wallets_to_create, batch_size=100)

print(f"✅ Created {len(wallets_to_create)} wallets")

# Get created wallets
wallets_dict = {}
for wallet in Wallet.objects.filter(owner_type="USER", owner_id__in=[u.id for u in created_users]):
    wallets_dict[wallet.owner_id] = wallet

# Create contributions for users (50% of users)
print(f"✅ Creating contributions...")
contribution_types = ["share", "savings", "fine"]
contributions_count = 0

for user in created_users[::2]:  # Every other user
    for _ in range(random.randint(1, 3)):  # 1-3 contributions per user
        contribution = Contribution(
            chama=yangu_chama,
            member=user,
            amount=Decimal(random.uniform(500, 5000)).quantize(Decimal('0.01')),
            contribution_type=random.choice(contribution_types),
            date=timezone.now() - timedelta(days=random.randint(1, 60)),
        )
        contributions_to_create.append(contribution)
        contributions_count += 1

if contributions_to_create:
    Contribution.objects.bulk_create(contributions_to_create, batch_size=100)
    print(f"   Created {contributions_count} contributions")

# Create loans for users (20% of users)
print(f"✅ Creating loans...")
loans_count = 0

for user in created_users[::5]:  # Every 5th user
    loan_amount = Decimal(random.uniform(10000, 100000)).quantize(Decimal('0.01'))
    loan = Loan(
        chama=yangu_chama,
        member=user,
        original_amount=loan_amount,
        outstanding_amount=loan_amount * Decimal(random.uniform(0.5, 1.0)),
        interest_rate=Decimal(random.uniform(0.05, 0.15)),
        status=random.choice([LoanStatus.ACTIVE, LoanStatus.PENDING]),
        loan_date=timezone.now() - timedelta(days=random.randint(30, 180)),
        due_date=timezone.now() + timedelta(days=random.randint(30, 180)),
    )
    loans_to_create.append(loan)
    loans_count += 1

if loans_to_create:
    Loan.objects.bulk_create(loans_to_create, batch_size=100)
    print(f"   Created {loans_count} loans")

# Refresh chama data
yangu_chama.refresh_from_db()

print("\n" + "=" * 120)
print("✅ BULK DATA GENERATION COMPLETE")
print("=" * 120)

print(f"\n📊 YANGU CHAMA STATISTICS:")
print(f"   Total Members: {yangu_chama.memberships.filter(status=MemberStatus.ACTIVE).count()}")
print(f"   Role Distribution:")
print(f"      • MEMBERS: {role_counts[MembershipRole.MEMBER]}")
print(f"      • TREASURERS: {role_counts[MembershipRole.TREASURER]}")
print(f"      • SECRETARIES: {role_counts[MembershipRole.SECRETARY]}")
print(f"      • AUDITORS: {role_counts[MembershipRole.AUDITOR]}")

# Calculate totals
total_wallets = Wallet.objects.filter(owner_type="USER", owner_id__in=[u.id for u in created_users]).count()
total_available = Wallet.objects.filter(owner_type="USER", owner_id__in=[u.id for u in created_users]).aggregate(
    total=models.Sum('available_balance')
)['total'] or Decimal('0')
total_locked = Wallet.objects.filter(owner_type="USER", owner_id__in=[u.id for u in created_users]).aggregate(
    total=models.Sum('locked_balance')
)['total'] or Decimal('0')

print(f"\n💰 WALLET STATISTICS:")
print(f"   Total Wallets: {total_wallets}")
print(f"   Total Available Balance: KES {total_available:,.2f}")
print(f"   Total Locked Balance: KES {total_locked:,.2f}")
print(f"   Grand Total: KES {total_available + total_locked:,.2f}")

print(f"\n📝 CONTRIBUTIONS STATISTICS:")
print(f"   Total Contributions: {Contribution.objects.filter(chama=yangu_chama, member__in=created_users).count()}")

total_contrib_amount = Contribution.objects.filter(chama=yangu_chama, member__in=created_users).aggregate(
    total=models.Sum('amount')
)['total'] or Decimal('0')
print(f"   Total Contributed: KES {total_contrib_amount:,.2f}")

print(f"\n🏦 LOAN STATISTICS:")
print(f"   Total Loans: {Loan.objects.filter(chama=yangu_chama, member__in=created_users).count()}")

total_loan_amount = Loan.objects.filter(chama=yangu_chama, member__in=created_users).aggregate(
    total=models.Sum('original_amount')
)['total'] or Decimal('0')
total_outstanding = Loan.objects.filter(chama=yangu_chama, member__in=created_users).aggregate(
    total=models.Sum('outstanding_amount')
)['total'] or Decimal('0')

print(f"   Total Loan Amount: KES {total_loan_amount:,.2f}")
print(f"   Total Outstanding: KES {total_outstanding:,.2f}")

# Calculate some stats
print(f"\n📈 SAMPLE USER VERIFICATION:")
sample_user = created_users[0]
sample_membership = Membership.objects.get(user=sample_user, chama=yangu_chama)
sample_wallet = wallets_dict.get(sample_user.id)
sample_contributions = Contribution.objects.filter(member=sample_user, chama=yangu_chama)
sample_loans = Loan.objects.filter(member=sample_user, chama=yangu_chama)

print(f"   User: {sample_user.full_name} ({sample_user.phone})")
print(f"   Role: {sample_membership.role}")
print(f"   Status: {sample_membership.status}")
print(f"   Phone Verified: {sample_user.phone_verified}")
if sample_wallet:
    print(f"   Wallet Available: KES {sample_wallet.available_balance:,.2f}")
    print(f"   Wallet Locked: KES {sample_wallet.locked_balance:,.2f}")
print(f"   Contributions: {sample_contributions.count()}")
print(f"   Loans: {sample_loans.count()}")

print("\n" + "=" * 120)
print("✅ YANGU CHAMA IS NOW FULLY POPULATED AND ACTIVE!")
print("=" * 120 + "\n")
