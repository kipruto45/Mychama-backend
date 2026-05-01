"""
Custom Authentication Backend for Phone-based Authentication

This backend extends Django's ModelBackend to support phone-based authentication
in addition to the default username-based authentication.
"""

import logging

from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

from apps.accounts.models import normalize_kenyan_phone

logger = logging.getLogger(__name__)

User = get_user_model()


class PhoneAuthenticationBackend(ModelBackend):
    """
    Authentication backend that supports phone-based login.
    
    This allows users to authenticate using their phone number instead of username.
    The phone number is normalized to E.164 format before lookup.
    """

    def authenticate(self, request, phone=None, password=None, **kwargs):
        if phone is None or password is None:
            return None
            
        try:
            # Normalize the phone number
            normalized_phone = normalize_kenyan_phone(phone)
        except (ValueError, TypeError) as e:
            logger.warning(f"Phone normalization failed: {e}")
            return None
            
        # Look up user by normalized phone number
        try:
            user = User.objects.get(phone=normalized_phone)
        except User.DoesNotExist:
            logger.debug(f"No user found with phone: {normalized_phone}")
            return None
        except User.MultipleObjectsReturned:
            logger.error(f"Multiple users found with phone: {normalized_phone}")
            return None
            
        # Check password
        if user.check_password(password):
            logger.info(f"User authenticated successfully: {user.phone}")
            return user
        else:
            logger.debug(f"Invalid password for user: {user.phone}")
            return None
            
    def user_can_authenticate(self, user):
        """
        Reject users with is_active=False.
        """
        is_active = getattr(user, 'is_active', None)
        return is_active is not False