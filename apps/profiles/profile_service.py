"""
Profile and KYC Service

Manages user profiles, KYC verification, and account security.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User

logger = logging.getLogger(__name__)


class ProfileService:
    """Service for managing user profiles and KYC."""

    @staticmethod
    def get_profile(user: User) -> dict:
        """
        Get user profile information.
        """
        from apps.profiles.models import UserProfile

        profile, created = UserProfile.objects.get_or_create(user=user)

        return {
            'user_id': str(user.id),
            'full_name': user.full_name,
            'email': user.email,
            'phone': user.phone,
            'avatar': profile.avatar.url if profile.avatar else None,
            'date_of_birth': profile.date_of_birth.isoformat() if profile.date_of_birth else None,
            'gender': profile.gender,
            'occupation': profile.occupation,
            'county': profile.county,
            'location': profile.location,
            'next_of_kin_name': profile.next_of_kin_name,
            'next_of_kin_phone': profile.next_of_kin_phone,
            'next_of_kin_relationship': profile.next_of_kin_relationship,
            'kyc_status': profile.kyc_status,
            'profile_completeness': profile.profile_completeness,
            'created_at': user.date_joined.isoformat(),
            'updated_at': profile.updated_at.isoformat(),
        }

    @staticmethod
    @transaction.atomic
    def update_profile(
        user: User,
        **kwargs,
    ) -> tuple[bool, str]:
        """
        Update user profile.
        Returns (success, message).
        """
        from apps.profiles.models import UserProfile

        try:
            profile, created = UserProfile.objects.get_or_create(user=user)

            # Update user fields
            if 'full_name' in kwargs:
                user.full_name = kwargs['full_name']
            if 'email' in kwargs:
                user.email = kwargs['email']

            user.save()

            # Update profile fields
            profile_fields = [
                'date_of_birth', 'gender', 'occupation', 'county',
                'location', 'next_of_kin_name', 'next_of_kin_phone',
                'next_of_kin_relationship',
            ]

            for field in profile_fields:
                if field in kwargs:
                    setattr(profile, field, kwargs[field])

            # Handle avatar upload
            if 'avatar' in kwargs:
                profile.avatar = kwargs['avatar']

            profile.save()

            # Calculate profile completeness
            profile.profile_completeness = ProfileService._calculate_completeness(profile)
            profile.save(update_fields=['profile_completeness', 'updated_at'])

            logger.info(f"Profile updated for user {user.full_name}")

            return True, "Profile updated"

        except Exception as e:
            logger.error(f"Failed to update profile: {e}")
            return False, "Failed to update profile"

    @staticmethod
    def _calculate_completeness(profile) -> float:
        """
        Calculate profile completeness percentage.
        """
        fields = [
            profile.user.full_name,
            profile.user.email,
            profile.user.phone,
            profile.date_of_birth,
            profile.gender,
            profile.occupation,
            profile.county,
            profile.location,
            profile.next_of_kin_name,
            profile.next_of_kin_phone,
            profile.next_of_kin_relationship,
            profile.avatar,
        ]

        filled = sum(1 for field in fields if field)
        total = len(fields)

        return (filled / total * 100) if total > 0 else 0

    @staticmethod
    def get_kyc_status(user: User) -> dict:
        """
        Get KYC verification status.
        """
        from apps.profiles.models import KYCDocument

        documents = KYCDocument.objects.filter(user=user)

        return {
            'status': user.profile.kyc_status if hasattr(user, 'profile') else 'pending',
            'documents': [
                {
                    'id': str(doc.id),
                    'document_type': doc.document_type,
                    'status': doc.status,
                    'uploaded_at': doc.created_at.isoformat(),
                    'verified_at': doc.verified_at.isoformat() if doc.verified_at else None,
                }
                for doc in documents
            ],
        }

    @staticmethod
    @transaction.atomic
    def submit_kyc(
        user: User,
        id_number: str,
        id_front_image,
        id_back_image,
        selfie_image,
    ) -> tuple[bool, str]:
        """
        Submit KYC documents for verification.
        Returns (success, message).
        """
        from apps.profiles.models import KYCDocument, UserProfile

        try:
            profile, created = UserProfile.objects.get_or_create(user=user)

            # Create KYC documents
            KYCDocument.objects.create(
                user=user,
                document_type='id_front',
                file=id_front_image,
                status='pending',
            )

            KYCDocument.objects.create(
                user=user,
                document_type='id_back',
                file=id_back_image,
                status='pending',
            )

            KYCDocument.objects.create(
                user=user,
                document_type='selfie',
                file=selfie_image,
                status='pending',
            )

            # Update profile
            profile.id_number = id_number
            profile.kyc_status = 'submitted'
            profile.kyc_submitted_at = timezone.now()
            profile.save(update_fields=[
                'id_number',
                'kyc_status',
                'kyc_submitted_at',
                'updated_at',
            ])

            logger.info(f"KYC submitted for user {user.full_name}")

            return True, "KYC documents submitted"

        except Exception as e:
            logger.error(f"Failed to submit KYC: {e}")
            return False, "Failed to submit KYC documents"

    @staticmethod
    @transaction.atomic
    def verify_kyc(
        user: User,
        verifier: User,
        approved: bool,
        notes: str = '',
    ) -> tuple[bool, str]:
        """
        Verify KYC documents.
        Returns (success, message).
        """
        from apps.profiles.models import KYCDocument, UserProfile

        try:
            profile = UserProfile.objects.get(user=user)

            # Update document statuses
            status = 'approved' if approved else 'rejected'
            KYCDocument.objects.filter(user=user).update(
                status=status,
                verified_by=verifier,
                verified_at=timezone.now(),
                verification_notes=notes,
            )

            # Update profile
            profile.kyc_status = status
            profile.kyc_verified_by = verifier
            profile.kyc_verified_at = timezone.now()
            profile.save(update_fields=[
                'kyc_status',
                'kyc_verified_by',
                'kyc_verified_at',
                'updated_at',
            ])

            logger.info(f"KYC verified for user {user.full_name}: {status}")

            return True, f"KYC {status}"

        except UserProfile.DoesNotExist:
            return False, "Profile not found"

    @staticmethod
    def get_verification_history(user: User) -> list[dict]:
        """
        Get KYC verification history.
        """
        from apps.profiles.models import KYCDocument

        documents = KYCDocument.objects.filter(user=user).order_by('-created_at')

        return [
            {
                'id': str(doc.id),
                'document_type': doc.document_type,
                'status': doc.status,
                'verified_by': doc.verified_by.full_name if doc.verified_by else None,
                'verified_at': doc.verified_at.isoformat() if doc.verified_at else None,
                'notes': doc.verification_notes,
                'created_at': doc.created_at.isoformat(),
            }
            for doc in documents
        ]

    @staticmethod
    def change_password(
        user: User,
        old_password: str,
        new_password: str,
    ) -> tuple[bool, str]:
        """
        Change user password.
        Returns (success, message).
        """
        from django.contrib.auth.hashers import check_password

        if not check_password(old_password, user.password):
            return False, "Invalid old password"

        user.set_password(new_password)
        user.save(update_fields=['password', 'updated_at'])

        logger.info(f"Password changed for user {user.full_name}")

        return True, "Password changed"

    @staticmethod
    def get_security_settings(user: User) -> dict:
        """
        Get security settings for a user.
        """
        from apps.profiles.models import SecuritySettings

        settings, created = SecuritySettings.objects.get_or_create(user=user)

        return {
            'two_factor_enabled': settings.two_factor_enabled,
            'biometric_enabled': settings.biometric_enabled,
            'login_notifications': settings.login_notifications,
            'session_timeout': settings.session_timeout,
            'trusted_devices_count': settings.trusted_devices.count(),
        }

    @staticmethod
    @transaction.atomic
    def update_security_settings(
        user: User,
        **kwargs,
    ) -> tuple[bool, str]:
        """
        Update security settings.
        Returns (success, message).
        """
        from apps.profiles.models import SecuritySettings

        try:
            settings, created = SecuritySettings.objects.get_or_create(user=user)

            for key, value in kwargs.items():
                if hasattr(settings, key):
                    setattr(settings, key, value)

            settings.save()

            return True, "Security settings updated"

        except Exception as e:
            logger.error(f"Failed to update security settings: {e}")
            return False, "Failed to update security settings"
