"""
KYC domain package.

Avoid importing services at module import time to prevent circular imports with accounts.models.
Import from `apps.accounts.kyc.services` directly instead.
"""

__all__ = []
