from django import forms
from django.contrib.auth import get_user_model

User = get_user_model()


class NotificationSettingsForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['email']  # Placeholder, add actual notification fields if needed