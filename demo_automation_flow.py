#!/usr/bin/env python3
"""
Demo Script: Test Complete Automation Flow
Tests the full chama lifecycle: create → invite → contribute → loan → repay → report
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from django.utils import timezone
from datetime import timedelta
import random

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus, Invite
from apps.finance.models import ContributionSchedule, Contribution
from apps.loans.models import Loan, LoanApplication
from apps.wallet.models import Wallet, Transaction
from apps.notifications.models import Notification


def create_demo_data():
    """Create demo data for testing the automation flow"""
    
    print("=" * 60)
    print("CREATING DEMO DATA FOR AUTOMATION TESTING")
    print("=" * 60)
    
    # Clean up existing demo data
    print("\n[1/10] Cleaning up existing demo data...")
    User.objects.filter(phone__startswith='+254700').delete()
    print("   ✓ Cleaned up existing demo users")
    
    # Create users
    print("\n[2/10] Creating demo users...")
    chama_admin = User.objects.create_user(
        phone='+254700000001',
        password='demo123',
        first_name='John',
        last_name='ChamaAdmin'
    )
    treasurer = User.objects.create_user(
        phone='+254700000002',
        password='demo123',
        first_name='Jane',
        last_name='Treasurer'
    )
    secretary = User.objects.create_user(
        phone='+254700000003',
        password='demo123',
        first_name='Mike',
        last_name='Secretary'
    )
    member1 = User.objects.create_user(
        phone='+254700000004',
        password='demo123',
        first_name='Alice',
        last_name='Member'
    )
    member2 = User.objects.create_user(
        phone='+254700000005',
        password='demo123',
        first_name='Bob',
        last_name='Member'
    )
    print(f"   ✓ Created {User.objects.count()} users")
    
    # Create chama
    print("\n[3/10] Creating chama...")
    chama = Chama.objects.create(
        name='Demo Chama',
        description='A demo chama for testing automations',
        contribution_amount=5000,
        contribution_schedule='MONTHLY',
        max_loan_amount=100000,
        min_loan_amount=5000,
        loan_interest_rate=12,
        penalty_rate=10,
        region='Nairobi',
        currency='KES'
    )
    print(f"   ✓ Created chama: {chama.name}")
    
    # Create memberships
    print("\n[4/10] Creating memberships...")
    m_admin = Membership.objects.create(
        user=chama_admin,
        chama=chama,
        role=MembershipRole.CHAMA_ADMIN,
        status=MemberStatus.ACTIVE,
        joined_at=timezone.now() - timedelta(days=180)
    )
    m_treas = Membership.objects.create(
        user=treasurer,
        chama=chama,
        role=MembershipRole.TREASURER,
        status=MemberStatus.ACTIVE,
        joined_at=timezone.now() - timedelta(days=150)
    )
    m_sec = Membership.objects.create(
        user=secretary,
        chama=chama,
        role=MembershipRole.SECRETARY,
        status=MemberStatus.ACTIVE,
        joined_at=timezone.now() - timedelta(days=120)
    )
    m_mem1 = Membership.objects.create(
        user=member1,
        chama=chama,
        role=MembershipRole.MEMBER,
        status=MemberStatus.ACTIVE,
        joined_at=timezone.now() - timedelta(days=90)
    )
    m_mem2 = Membership.objects.create(
        user=member2,
        chama=chama,
        role=MembershipRole.MEMBER,
        status=MemberStatus.ACTIVE,
        joined_at=timezone.now() - timedelta(days=60)
    )
    print(f"   ✓ Created {Membership.objects.count()} memberships")
    
    # Create wallets
    print("\n[5/10] Creating wallets...")
    for m in [m_admin, m_treas, m_sec, m_mem1, m_mem2]:
        wallet, _ = Wallet.objects.get_or_create(
            member=m,
            defaults={'balance': random.randint(10000, 100000)}
        )
    print(f"   ✓ Created {Wallet.objects.count()} wallets")
    
    # Create contribution schedules
    print("\n[6/10] Creating contribution schedules...")
    members = [m_admin, m_treas, m_sec, m_mem1, m_mem2]
    for i, member in enumerate(members):
        for months_ago in range(6):
            due_date = timezone.now() - timedelta(days=30 * months_ago)
            status = 'PAID' if i <= months_ago else ('OVERDUE' if months_ago > 0 else 'PENDING')
            schedule, _ = ContributionSchedule.objects.get_or_create(
                member=member,
                chama=chama,
                due_date=due_date.date(),
                defaults={
                    'amount': chama.contribution_amount,
                    'amount_paid': chama.contribution_amount if status == 'PAID' else 0,
                    'status': status,
                }
            )
            if status == 'PAID':
                schedule.amount_paid = chama.contribution_amount
                schedule.save()
    print(f"   ✓ Created {ContributionSchedule.objects.count()} contribution schedules")
    
    # Create a loan application
    print("\n[7/10] Creating loan application...")
    loan = Loan.objects.create(
        member=m_mem1,
        chama=chama,
        amount_applied=30000,
        purpose='Business',
        term_months=6,
        status='PENDING',
        risk_score=35
    )
    print(f"   ✓ Created loan application: {loan.id}")
    
    # Create security alerts
    print("\n[8/10] Creating security alerts...")
    Notification.objects.create(
        user=member1,
        title='Login from new device',
        message='Your account was accessed from a new iPhone device',
        notification_type='LOGIN_ALERT',
        severity='MEDIUM',
        chama=chama
    )
    Notification.objects.create(
        user=treasurer,
        title='Multiple failed login attempts',
        message='3 failed login attempts detected',
        notification_type='SECURITY_ALERT',
        severity='HIGH',
        chama=chama
    )
    print(f"   ✓ Created {Notification.objects.count()} security alerts")
    
    # Create sample transactions
    print("\n[9/10] Creating sample transactions...")
    for member in members[:3]:
        wallet = Wallet.objects.get(member=member)
        Transaction.objects.create(
            member=member,
            chama=chama,
            wallet=wallet,
            transaction_type='CONTRIBUTION',
            amount=5000,
            status='SUCCESS',
            description='Monthly contribution'
        )
    print(f"   ✓ Created {Transaction.objects.count()} transactions")
    
    # Print demo credentials
    print("\n" + "=" * 60)
    print("DEMO ACCOUNTS (password: demo123)")
    print("=" * 60)
    print(f"Chama Admin: {chama_admin.phone}")
    print(f"Treasurer:   {treasurer.phone}")
    print(f"Secretary:   {secretary.phone}")
    print(f"Member 1:    {member1.phone}")
    print(f"Member 2:    {member2.phone}")
    print("=" * 60)
    
    # Print API endpoints to test
    print("\n[10/10] API ENDPOINTS TO TEST")
    print("=" * 60)
    chama_id = str(chama.id)
    member_id = str(m_mem1.id)
    loan_id = str(loan.id)
    
    print(f"""
# Get compliance score
curl -H "Authorization: Bearer <TOKEN>" \\
  "http://localhost:8000/api/v1/automations/compliance/?member_id={member_id}&chama_id={chama_id}"

# Check loan eligibility  
curl -X POST -H "Authorization: Bearer <TOKEN>" \\
  -H "Content-Type: application/json" \\
  -d '{{"member_id": "{member_id}", "chama_id": "{chama_id}", "amount": 30000, "term_months": 6}}' \\
  "http://localhost:8000/api/v1/automations/loans/eligibility/"

# Get security alerts
curl -H "Authorization: Bearer <TOKEN>" \\
  "http://localhost:8000/api/v1/automations/security/alerts/?chama_id={chama_id}"

# Get effective role
curl -H "Authorization: Bearer <TOKEN>" \\
  "http://localhost:8000/api/v1/automations/effective-role/{member_id}/"

# Get loan approval queue
curl -H "Authorization: Bearer <TOKEN>" \\
  "http://localhost:8000/api/v1/automations/loans/queue/?chama_id={chama_id}&status=pending"

# Check withdrawal anomaly
curl -X POST -H "Authorization: Bearer <TOKEN>" \\
  -H "Content-Type: application/json" \\
  -d '{{"member_id": "{member_id}", "chama_id": "{chama_id}", "amount": 50000}}' \\
  "http://localhost:8000/api/v1/automations/anomaly/withdrawal/"

# Get audit logs
curl -H "Authorization: Bearer <TOKEN>" \\
  "http://localhost:8000/api/v1/automations/audit/?chama_id={chama_id}"
""")
    
    print("\n✅ Demo data creation complete!")
    print("\nRun tests with:")
    print("  pytest apps/automations/tests/test_mobile_views.py -v")
    
    return chama


if __name__ == '__main__':
    create_demo_data()
