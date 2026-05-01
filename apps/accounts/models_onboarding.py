"""
Onboarding Progress Model

Tracks user onboarding progress and completion status.
"""

import uuid

from django.conf import settings
from django.db import models


class OnboardingProgress(models.Model):
    """
    Tracks onboarding progress for users.
    Can be global or per-chama.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='onboarding_progress',
    )
    chama = models.ForeignKey(
        'chama.Chama',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='onboarding_progress',
    )
    
    # Progress tracking
    completed_steps = models.JSONField(
        default=list,
        blank=True,
        help_text='List of completed step IDs',
    )
    
    # Completion status
    completed_at = models.DateTimeField(null=True, blank=True)
    skipped = models.BooleanField(default=False)
    skipped_at = models.DateTimeField(null=True, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', 'chama']),
            models.Index(fields=['user', 'completed_at']),
            models.Index(fields=['chama', 'completed_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'chama'],
                name='unique_onboarding_progress_per_user_chama',
            ),
        ]

    def __str__(self):
        chama_name = self.chama.name if self.chama else 'Global'
        return f"Onboarding for {self.user} @ {chama_name}"

    @property
    def is_complete(self) -> bool:
        """Check if onboarding is complete."""
        return self.completed_at is not None

    @property
    def progress_percentage(self) -> float:
        """Calculate progress percentage."""
        if not self.completed_steps:
            return 0.0
        
        # This is a simplified calculation
        # In practice, you'd compare against total steps
        return min(100.0, len(self.completed_steps) * 16.67)  # 6 steps = 100%
