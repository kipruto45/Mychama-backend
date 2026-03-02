#!/usr/bin/env python
"""
Script to create a TREASURER user for loan approval testing.
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from django.contrib.auth import get_user_model
from apps.chama.models import Membership, MembershipRole, MemberStatus, Chama

User = get_user_model()

# Check if treasurer already exists
existing_treasurer = User.objects.filter(phone='+254700000005').first()
if existing_treasurer:
    print(f"Treasurer user already exists: {existing_treasurer.phone}")
    treasurer = existing_treasurer
else:
    # Create treasurer user
    treasurer = User.objects.create_user(
        phone='+254700000005',
        password='password123',
        full_name='Bob Treasurer',
        email='treasurer@example.com',
    )
    print(f"Created user: {treasurer.phone}")

# Add treasurer to the default chama
chama = Chama.objects.first()
if chama:
    membership, created = Membership.objects.get_or_create(
        user=treasurer,
        chama=chama,
        defaults={
            'role': MembershipRole.TREASURER,
            'status': MemberStatus.ACTIVE,
            'is_active': True,
            'is_approved': True,
        }
    )
    if created:
        print(f"Added treasurer to chama: {chama.name}")
    else:
        membership.role = MembershipRole.TREASURER
        membership.status = MemberStatus.ACTIVE
        membership.is_active = True
        membership.is_approved = True
        membership.save()
        print(f"Updated membership role to TREASURER")
else:
    print("No chama found!")

print("\n" + "="*50)
print("TREASURER USER DETAILS")
print("="*50)
print(f"Phone: +254700000005")
print(f"Password: password123")
print(f"Role: TREASURER")
print("="*50)
