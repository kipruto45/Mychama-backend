"""
Settings and Preferences Service

Manages user settings, theme preferences, and app configuration.
"""

import logging

from django.db import transaction

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class SettingsService:
    """Service for managing settings and preferences."""

    @staticmethod
    def get_user_settings(user: User) -> dict:
        """
        Get all settings for a user.
        """
        from apps.settings.models import UserSettings

        try:
            settings = UserSettings.objects.get(user=user)
            return {
                'theme': settings.theme,
                'language': settings.language,
                'notifications': {
                    'email': settings.email_notifications,
                    'sms': settings.sms_notifications,
                    'push': settings.push_notifications,
                    'contribution_reminders': settings.contribution_reminders,
                    'meeting_reminders': settings.meeting_reminders,
                    'loan_reminders': settings.loan_reminders,
                    'announcement_notifications': settings.announcement_notifications,
                },
                'privacy': {
                    'show_profile': settings.show_profile,
                    'show_contributions': settings.show_contributions,
                    'show_loans': settings.show_loans,
                },
                'display': {
                    'currency': settings.currency,
                    'date_format': settings.date_format,
                    'time_format': settings.time_format,
                },
            }
        except UserSettings.DoesNotExist:
            # Return default settings
            return {
                'theme': 'light',
                'language': 'en',
                'notifications': {
                    'email': True,
                    'sms': True,
                    'push': True,
                    'contribution_reminders': True,
                    'meeting_reminders': True,
                    'loan_reminders': True,
                    'announcement_notifications': True,
                },
                'privacy': {
                    'show_profile': True,
                    'show_contributions': True,
                    'show_loans': True,
                },
                'display': {
                    'currency': 'KES',
                    'date_format': 'YYYY-MM-DD',
                    'time_format': 'HH:mm',
                },
            }

    @staticmethod
    @transaction.atomic
    def update_user_settings(
        user: User,
        settings_data: dict,
    ) -> tuple[bool, str]:
        """
        Update user settings.
        Returns (success, message).
        """
        from apps.settings.models import UserSettings

        try:
            settings, created = UserSettings.objects.get_or_create(
                user=user,
                defaults=settings_data,
            )

            if not created:
                for key, value in settings_data.items():
                    if hasattr(settings, key):
                        setattr(settings, key, value)
                settings.save()

            return True, "Settings updated"

        except Exception as e:
            logger.error(
                f"Failed to update user settings: {e}"
            )
            return False, "Failed to update settings"

    @staticmethod
    def get_chama_settings(chama: Chama) -> dict:
        """
        Get settings for a chama.
        """
        from apps.settings.models import ChamaSettings

        try:
            settings = ChamaSettings.objects.get(chama=chama)
            return {
                'contribution': {
                    'amount': settings.contribution_amount,
                    'frequency': settings.contribution_frequency,
                    'due_day': settings.contribution_due_day,
                    'grace_period_days': settings.grace_period_days,
                    'late_fee_percentage': settings.late_fee_percentage,
                },
                'loans': {
                    'enabled': settings.loans_enabled,
                    'max_multiplier': settings.max_loan_multiplier,
                    'interest_rate': settings.interest_rate,
                    'max_term_months': settings.max_term_months,
                },
                'meetings': {
                    'frequency': settings.meeting_frequency,
                    'day_of_week': settings.meeting_day_of_week,
                    'time': settings.meeting_time,
                    'duration': settings.meeting_duration,
                },
                'membership': {
                    'join_policy': settings.join_policy,
                    'max_members': settings.max_members,
                    'require_kyc': settings.require_kyc,
                },
            }
        except ChamaSettings.DoesNotExist:
            # Return default settings
            return {
                'contribution': {
                    'amount': 1000,
                    'frequency': 'monthly',
                    'due_day': 1,
                    'grace_period_days': 7,
                    'late_fee_percentage': 5,
                },
                'loans': {
                    'enabled': True,
                    'max_multiplier': 3,
                    'interest_rate': 10,
                    'max_term_months': 12,
                },
                'meetings': {
                    'frequency': 'monthly',
                    'day_of_week': 6,
                    'time': '10:00',
                    'duration': 120,
                },
                'membership': {
                    'join_policy': 'approval_required',
                    'max_members': 20,
                    'require_kyc': True,
                },
            }

    @staticmethod
    @transaction.atomic
    def update_chama_settings(
        chama: Chama,
        settings_data: dict,
        updater: User,
    ) -> tuple[bool, str]:
        """
        Update chama settings.
        Returns (success, message).
        """
        from apps.chama.permissions import Permission, PermissionChecker
        from apps.settings.models import ChamaSettings

        # Check if updater has permission
        if not PermissionChecker.has_permission(
            updater,
            Permission.CAN_MANAGE_CHAMA_SETTINGS,
            str(chama.id),
        ):
            return False, "Permission denied"

        try:
            settings, created = ChamaSettings.objects.get_or_create(
                chama=chama,
                defaults=settings_data,
            )

            if not created:
                for key, value in settings_data.items():
                    if hasattr(settings, key):
                        setattr(settings, key, value)
                settings.save()

            return True, "Settings updated"

        except Exception as e:
            logger.error(
                f"Failed to update chama settings: {e}"
            )
            return False, "Failed to update settings"

    @staticmethod
    def get_notification_preferences(user: User) -> dict:
        """
        Get notification preferences for a user.
        """
        from apps.settings.models import NotificationPreference

        try:
            preferences = NotificationPreference.objects.get(user=user)
            return {
                'email_enabled': preferences.email_enabled,
                'sms_enabled': preferences.sms_enabled,
                'push_enabled': preferences.push_enabled,
                'contribution_reminders': preferences.contribution_reminders,
                'meeting_reminders': preferences.meeting_reminders,
                'loan_reminders': preferences.loan_reminders,
                'announcement_notifications': preferences.announcement_notifications,
                'reminder_days_before': preferences.reminder_days_before,
                'quiet_hours_start': preferences.quiet_hours_start,
                'quiet_hours_end': preferences.quiet_hours_end,
            }
        except NotificationPreference.DoesNotExist:
            # Return default preferences
            return {
                'email_enabled': True,
                'sms_enabled': True,
                'push_enabled': True,
                'contribution_reminders': True,
                'meeting_reminders': True,
                'loan_reminders': True,
                'announcement_notifications': True,
                'reminder_days_before': 3,
                'quiet_hours_start': '22:00',
                'quiet_hours_end': '07:00',
            }

    @staticmethod
    @transaction.atomic
    def update_notification_preferences(
        user: User,
        preferences_data: dict,
    ) -> tuple[bool, str]:
        """
        Update notification preferences for a user.
        Returns (success, message).
        """
        from apps.settings.models import NotificationPreference

        try:
            preferences, created = NotificationPreference.objects.get_or_create(
                user=user,
                defaults=preferences_data,
            )

            if not created:
                for key, value in preferences_data.items():
                    if hasattr(preferences, key):
                        setattr(preferences, key, value)
                preferences.save()

            return True, "Preferences updated"

        except Exception as e:
            logger.error(
                f"Failed to update notification preferences: {e}"
            )
            return False, "Failed to update preferences"

    @staticmethod
    def get_privacy_settings(user: User) -> dict:
        """
        Get privacy settings for a user.
        """
        from apps.settings.models import PrivacySettings

        try:
            settings = PrivacySettings.objects.get(user=user)
            return {
                'profile_visibility': settings.profile_visibility,
                'show_email': settings.show_email,
                'show_phone': settings.show_phone,
                'show_contributions': settings.show_contributions,
                'show_loans': settings.show_loans,
                'allow_search': settings.allow_search,
            }
        except PrivacySettings.DoesNotExist:
            # Return default settings
            return {
                'profile_visibility': 'members_only',
                'show_email': False,
                'show_phone': False,
                'show_contributions': True,
                'show_loans': True,
                'allow_search': True,
            }

    @staticmethod
    @transaction.atomic
    def update_privacy_settings(
        user: User,
        settings_data: dict,
    ) -> tuple[bool, str]:
        """
        Update privacy settings for a user.
        Returns (success, message).
        """
        from apps.settings.models import PrivacySettings

        try:
            settings, created = PrivacySettings.objects.get_or_create(
                user=user,
                defaults=settings_data,
            )

            if not created:
                for key, value in settings_data.items():
                    if hasattr(settings, key):
                        setattr(settings, key, value)
                settings.save()

            return True, "Privacy settings updated"

        except Exception as e:
            logger.error(
                f"Failed to update privacy settings: {e}"
            )
            return False, "Failed to update privacy settings"

    @staticmethod
    def get_app_settings() -> dict:
        """
        Get global app settings.
        """
        from apps.settings.models import AppSettings

        try:
            settings = AppSettings.objects.first()
            if settings:
                return {
                    'app_name': settings.app_name,
                    'app_version': settings.app_version,
                    'maintenance_mode': settings.maintenance_mode,
                    'registration_enabled': settings.registration_enabled,
                    'default_currency': settings.default_currency,
                    'default_language': settings.default_language,
                    'support_email': settings.support_email,
                    'support_phone': settings.support_phone,
                }
        except AppSettings.DoesNotExist:
            pass

        # Return default settings
        return {
            'app_name': 'Digital Chama',
            'app_version': '1.0.0',
            'maintenance_mode': False,
            'registration_enabled': True,
            'default_currency': 'KES',
            'default_language': 'en',
            'support_email': 'support@digitalchama.com',
            'support_phone': '+254700000000',
        }

    @staticmethod
    def get_theme_options() -> list[dict]:
        """
        Get available theme options.
        """
        return [
            {
                'id': 'light',
                'name': 'Light',
                'description': 'Light theme for daytime use',
            },
            {
                'id': 'dark',
                'name': 'Dark',
                'description': 'Dark theme for nighttime use',
            },
            {
                'id': 'system',
                'name': 'System',
                'description': 'Follow system theme settings',
            },
        ]

    @staticmethod
    def get_language_options() -> list[dict]:
        """
        Get available language options.
        """
        return [
            {
                'id': 'en',
                'name': 'English',
                'native_name': 'English',
            },
            {
                'id': 'sw',
                'name': 'Swahili',
                'native_name': 'Kiswahili',
            },
        ]

    @staticmethod
    def get_currency_options() -> list[dict]:
        """
        Get available currency options.
        """
        return [
            {
                'id': 'KES',
                'name': 'Kenyan Shilling',
                'symbol': 'KES',
            },
            {
                'id': 'USD',
                'name': 'US Dollar',
                'symbol': '$',
            },
            {
                'id': 'EUR',
                'name': 'Euro',
                'symbol': '€',
            },
            {
                'id': 'GBP',
                'name': 'British Pound',
                'symbol': '£',
            },
        ]
