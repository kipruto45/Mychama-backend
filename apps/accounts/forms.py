from django import forms
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.forms import PasswordChangeForm, ReadOnlyPasswordHashField
from django.utils import timezone

from apps.accounts.models import OTPPurpose, PasswordResetToken, User
from core.utils import normalize_kenyan_phone


class CustomUserCreationForm(forms.ModelForm):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(
        label="Password confirmation", widget=forms.PasswordInput
    )

    class Meta:
        model = User
        fields = ("phone", "full_name", "email", "is_active", "is_staff")

    def clean_phone(self):
        phone = self.cleaned_data.get("phone")
        return normalize_kenyan_phone(phone)

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")

        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("Passwords do not match.")

        return password2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class CustomUserChangeForm(forms.ModelForm):
    password = ReadOnlyPasswordHashField()

    class Meta:
        model = User
        fields = (
            "phone",
            "full_name",
            "email",
            "password",
            "is_active",
            "is_staff",
            "is_superuser",
            "groups",
            "user_permissions",
            "two_factor_enabled",
            "two_factor_method",
        )

    def clean_phone(self):
        phone = self.cleaned_data.get("phone")
        return normalize_kenyan_phone(phone)


class LoginForm(forms.Form):
    username = forms.CharField(max_length=16)
    password = forms.CharField(widget=forms.PasswordInput)
    remember_me = forms.BooleanField(required=False)

    def clean_username(self):
        username = self.cleaned_data.get("username", "")
        return normalize_kenyan_phone(username)


class RegisterForm(forms.Form):
    phone = forms.CharField(max_length=16)
    full_name = forms.CharField(max_length=255)
    email = forms.EmailField(required=False)
    password = forms.CharField(widget=forms.PasswordInput)
    password_confirm = forms.CharField(widget=forms.PasswordInput)

    def clean_phone(self):
        phone = normalize_kenyan_phone(self.cleaned_data.get("phone", ""))
        if User.objects.filter(phone=phone).exists():
            raise forms.ValidationError("A user with this phone already exists.")
        return phone

    def clean_email(self):
        email = str(self.cleaned_data.get("email", "")).strip().lower()
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        password_confirm = cleaned_data.get("password_confirm")
        if password and password_confirm and password != password_confirm:
            self.add_error("password_confirm", "Passwords do not match.")
        if password:
            validate_password(password)
        return cleaned_data


class OTPVerifyPublicForm(forms.Form):
    phone = forms.CharField(max_length=16)
    code = forms.CharField(max_length=6)
    purpose = forms.ChoiceField(
        choices=[(OTPPurpose.VERIFY_PHONE, "Verify phone")],
        initial=OTPPurpose.VERIFY_PHONE,
    )

    def clean_phone(self):
        return normalize_kenyan_phone(self.cleaned_data.get("phone", ""))

    def clean_code(self):
        code = str(self.cleaned_data.get("code", "")).strip()
        if not code.isdigit() or len(code) != 6:
            raise forms.ValidationError("Enter a valid 6-digit OTP.")
        return code


class PasswordResetRequestForm(forms.Form):
    phone = forms.CharField(max_length=16)

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "")
        return normalize_kenyan_phone(phone)


class ResetPasswordConfirmForm(forms.Form):
    token = forms.CharField(widget=forms.HiddenInput)
    new_password1 = forms.CharField(widget=forms.PasswordInput)
    new_password2 = forms.CharField(widget=forms.PasswordInput)

    def clean_token(self):
        raw_token = str(self.cleaned_data.get("token", "")).strip()
        if not raw_token:
            raise forms.ValidationError("Reset token is required.")

        token_hash = PasswordResetToken.hash_token(raw_token)
        token_obj = (
            PasswordResetToken.objects.select_related("user")
            .filter(token_hash=token_hash)
            .order_by("-created_at")
            .first()
        )
        if not token_obj or not token_obj.is_usable:
            raise forms.ValidationError("Reset token is invalid or expired.")

        self.token_obj = token_obj
        return raw_token

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("new_password1")
        password2 = cleaned_data.get("new_password2")

        if password1 and password2 and password1 != password2:
            self.add_error("new_password2", "Passwords do not match.")

        token_obj = getattr(self, "token_obj", None)
        if token_obj and password1:
            validate_password(password1, user=token_obj.user)

        return cleaned_data

    def save(self):
        token_obj = self.token_obj
        user = token_obj.user
        user.set_password(self.cleaned_data["new_password1"])
        user.save(update_fields=["password"])

        PasswordResetToken.objects.filter(user=user, used_at__isnull=True).update(
            used_at=timezone.now()
        )
        return user


# Backward compatibility alias.
PasswordResetForm = PasswordResetRequestForm


class ChangePasswordForm(PasswordChangeForm):
    pass


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("full_name", "email", "phone")

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "")
        return normalize_kenyan_phone(phone)
