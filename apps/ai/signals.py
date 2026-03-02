# AI app signals
# This file is imported in apps.py to ensure signals are loaded

from django.db.models.signals import post_save
from django.dispatch import receiver

# Add signal handlers here as needed