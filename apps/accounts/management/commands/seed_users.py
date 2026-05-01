"""
Management command to seed test users for the Digital Chama system.
Creates users with different roles: ADMIN, SECRETARY, TREASURER, MEMBER
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus

User = get_user_model()


class Command(BaseCommand):
    help = 'Seed the database with test users and chama memberships'

    def handle(self, *args, **options):
        self.stdout.write('Creating test users...')
        
        # Create Admin User
        admin_user, created = User.objects.get_or_create(
            phone='+254700000001',
            defaults={
                'full_name': 'System Admin',
                'email': 'admin@digitalchama.co.ke',
                'is_staff': True,
                'is_superuser': True,
                'is_active': True,
                'phone_verified': True,
            }
        )
        if created:
            admin_user.set_password('Admin123!')
            admin_user.save()
            self.stdout.write(self.style.SUCCESS(f'Created admin user: {admin_user.phone}'))
        else:
            self.stdout.write(f'Admin user already exists: {admin_user.phone}')

        # Create Secretary User
        secretary_user, created = User.objects.get_or_create(
            phone='+254700000002',
            defaults={
                'full_name': 'Jane Secretary',
                'email': 'secretary@digitalchama.co.ke',
                'is_staff': True,
                'is_active': True,
                'phone_verified': True,
            }
        )
        if created:
            secretary_user.set_password('Secretary123!')
            secretary_user.save()
            self.stdout.write(self.style.SUCCESS(f'Created secretary user: {secretary_user.phone}'))
        else:
            self.stdout.write(f'Secretary user already exists: {secretary_user.phone}')

        # Create Treasurer User
        treasurer_user, created = User.objects.get_or_create(
            phone='+254700000003',
            defaults={
                'full_name': 'John Treasurer',
                'email': 'treasurer@digitalchama.co.ke',
                'is_active': True,
                'phone_verified': True,
            }
        )
        if created:
            treasurer_user.set_password('Treasurer123!')
            treasurer_user.save()
            self.stdout.write(self.style.SUCCESS(f'Created treasurer user: {treasurer_user.phone}'))
        else:
            self.stdout.write(f'Treasurer user already exists: {treasurer_user.phone}')

        # Create Member Users
        member_users_data = [
            ('+254700000004', 'Alice Member', 'alice@digitalchama.co.ke', 'Member123!'),
            ('+254700000005', 'Bob Member', 'bob@digitalchama.co.ke', 'Member123!'),
            ('+254700000006', 'Charlie Member', 'charlie@digitalchama.co.ke', 'Member123!'),
            ('+254700000007', 'Diana Member', 'diana@digitalchama.co.ke', 'Member123!'),
            ('+254700000008', 'Eve Member', 'eve@digitalchama.co.ke', 'Member123!'),
        ]

        member_users = []
        for phone, name, email, password in member_users_data:
            user, created = User.objects.get_or_create(
                phone=phone,
                defaults={
                    'full_name': name,
                    'email': email,
                    'is_active': True,
                    'phone_verified': True,
                }
            )
            if created:
                user.set_password(password)
                user.save()
                self.stdout.write(self.style.SUCCESS(f'Created member user: {user.phone}'))
            else:
                self.stdout.write(f'Member user already exists: {user.phone}')
            member_users.append(user)

        # Create a test Chama
        chama, created = Chama.objects.get_or_create(
            name='Demo Chama',
            defaults={
                'description': 'A demo chama for testing',
                'join_code': 'DEMO2024',
                'allow_public_join': True,
                'require_approval': False,
                'max_members': 50,
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'Created chama: {chama.name}'))
        else:
            self.stdout.write(f'Chama already exists: {chama.name}')

        # Create memberships
        self.stdout.write('Creating memberships...')

        # Admin as ADMIN
        membership, created = Membership.objects.get_or_create(
            user=admin_user,
            chama=chama,
            defaults={
                'status': MemberStatus.ACTIVE,
                'role': MembershipRole.ADMIN,
                'is_approved': True,
                'is_active': True,
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS('Admin membership created'))

        # Secretary as SECRETARY
        membership, created = Membership.objects.get_or_create(
            user=secretary_user,
            chama=chama,
            defaults={
                'status': MemberStatus.ACTIVE,
                'role': MembershipRole.SECRETARY,
                'is_approved': True,
                'is_active': True,
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS('Secretary membership created'))

        # Treasurer as TREASURER
        membership, created = Membership.objects.get_or_create(
            user=treasurer_user,
            chama=chama,
            defaults={
                'status': MemberStatus.ACTIVE,
                'role': MembershipRole.TREASURER,
                'is_approved': True,
                'is_active': True,
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS('Treasurer membership created'))

        # Members as MEMBER
        for user in member_users:
            membership, created = Membership.objects.get_or_create(
                user=user,
                chama=chama,
                defaults={
                    'status': MemberStatus.ACTIVE,
                    'role': MembershipRole.MEMBER,
                    'is_approved': True,
                    'is_active': True,
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'Member membership created for {user.phone}'))

        self.stdout.write(self.style.SUCCESS('\n=== Seed Complete ==='))
        self.stdout.write('\nLogin credentials:')
        self.stdout.write('=' * 50)
        self.stdout.write('ADMIN:    +254700000001 / Admin123!')
        self.stdout.write('SECRETARY: +254700000002 / Secretary123!')
        self.stdout.write('TREASURER: +254700000003 / Treasurer123!')
        self.stdout.write('MEMBER:   +254700000004 / Member123! (and +254700000005-008)')
        self.stdout.write('=' * 50)
        self.stdout.write(f'\nChama: {chama.name}')
        self.stdout.write(f'Join Code: {chama.join_code}')
