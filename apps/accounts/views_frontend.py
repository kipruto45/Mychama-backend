import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import (
    authenticate,
    login,
    update_session_auth_hash,
)
from django.contrib.auth import (
    logout as django_logout,
)
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.generic import TemplateView

from apps.accounts.views_dashboards import resolve_dashboard_route_for_request
from apps.chama.models import Membership, MemberStatus
from apps.security.models import DeviceSession, LoginAttempt
from apps.security.services import SecurityService
from core.utils import normalize_kenyan_phone

from .forms import (
    ChangePasswordForm,
    LoginForm,
    OTPVerifyPublicForm,
    PasswordResetRequestForm,
    ProfileForm,
    RegisterForm,
    ResetPasswordConfirmForm,
)
from .models import OTPPurpose, PasswordResetToken, User


class LoginView(TemplateView):
    template_name = "auth/login.html"

    @method_decorator(never_cache)
    @method_decorator(csrf_protect)
    def dispatch(self, *args, **kwargs):
        if self.request.user.is_authenticated:
            return redirect(resolve_dashboard_route_for_request(self.request))
        return super().dispatch(*args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = LoginForm()
        return context

    def post(self, request, *args, **kwargs):
        form = LoginForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data["username"]
            password = form.cleaned_data["password"]
            remember_me = bool(form.cleaned_data.get("remember_me"))
            user_agent = request.META.get("HTTP_USER_AGENT", "")
            ip_address = request.META.get("REMOTE_ADDR")
            identifier = username
            try:
                identifier = normalize_kenyan_phone(username)
            except ValueError:
                identifier = str(username).strip()

            if SecurityService.is_locked(identifier=identifier):
                messages.error(
                    request,
                    "Too many failed login attempts. Please try again later.",
                )
                SecurityService.record_login_attempt(
                    identifier=identifier,
                    ip_address=ip_address,
                    device_info=user_agent,
                    success=False,
                    user=None,
                )
                context = self.get_context_data()
                context["form"] = form
                return self.render_to_response(context)

            user = authenticate(request, phone=username, password=password)
            if user is not None:
                login(request, user)
                SecurityService.record_login_attempt(
                    identifier=identifier,
                    ip_address=ip_address,
                    device_info=user_agent,
                    success=True,
                    user=user,
                )
                SecurityService.clear_identifier_locks(identifier=identifier)
                device_name = request.headers.get(
                    "X-Device-Name", ""
                ) or request.META.get(
                    "HTTP_SEC_CH_UA_PLATFORM",
                    "web",
                )
                session_key = request.session.session_key or ""
                if not session_key:
                    request.session.save()
                    session_key = request.session.session_key or ""
                SecurityService.register_device_session(
                    user=user,
                    chama=None,
                    device_name=device_name,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    session_key=session_key,
                )
                if not remember_me:
                    request.session.set_expiry(0)
                else:
                    request.session.set_expiry(
                        int(getattr(settings, "SESSION_COOKIE_AGE", 1209600))
                    )
                next_url = request.GET.get(
                    "next", resolve_dashboard_route_for_request(request)
                )
                return redirect(next_url)
            else:
                SecurityService.record_login_attempt(
                    identifier=identifier,
                    ip_address=ip_address,
                    device_info=user_agent,
                    success=False,
                    user=None,
                )
                SecurityService.maybe_lock_after_failure(
                    identifier=identifier,
                    user=None,
                    reason="frontend_login_failed",
                )
                messages.error(request, "Invalid phone number or password.")
        else:
            messages.error(request, "Please correct the errors below.")

        context = self.get_context_data()
        context["form"] = form
        return self.render_to_response(context)


class RegisterView(TemplateView):
    template_name = "auth/register.html"

    @method_decorator(never_cache)
    @method_decorator(csrf_protect)
    def dispatch(self, *args, **kwargs):
        if self.request.user.is_authenticated:
            return redirect(resolve_dashboard_route_for_request(self.request))
        return super().dispatch(*args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = kwargs.get("form") or RegisterForm()
        return context

    def post(self, request, *args, **kwargs):
        from apps.accounts.services import OTPDeliveryError, OTPRateLimitError, OTPService

        form = RegisterForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        phone=form.cleaned_data["phone"],
                        password=form.cleaned_data["password"],
                        full_name=form.cleaned_data["full_name"],
                        email=form.cleaned_data.get("email", ""),
                    )
                    otp_token, plain_code = OTPService.generate_otp(
                        phone=user.phone,
                        user=user,
                        purpose=OTPPurpose.VERIFY_PHONE,
                        delivery_method="sms",
                    )
                    OTPService.send_otp(user.phone, otp_token, plain_code, user)
            except (OTPRateLimitError, OTPDeliveryError) as exc:
                messages.error(request, str(exc))
                return self.render_to_response(self.get_context_data(form=form))

            request.session["pending_verification_phone"] = user.phone
            messages.success(
                request,
                "Account created. Enter the OTP sent to your phone to verify.",
            )
            return redirect(f"{reverse('auth:verify_phone_otp')}?phone={user.phone}")

        messages.error(request, "Please correct the highlighted fields.")
        return self.render_to_response(self.get_context_data(form=form))


class VerifyPhoneOTPView(TemplateView):
    template_name = "auth/verify_phone_otp.html"

    @method_decorator(never_cache)
    @method_decorator(csrf_protect)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def _resolve_phone(self):
        phone = str(
            self.request.POST.get("phone")
            or self.request.GET.get("phone")
            or self.request.session.get("pending_verification_phone")
            or ""
        ).strip()
        if not phone:
            return ""
        try:
            return normalize_kenyan_phone(phone)
        except ValueError:
            return ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        phone = self._resolve_phone()
        initial = {"phone": phone, "purpose": OTPPurpose.VERIFY_PHONE}
        context["form"] = kwargs.get("form") or OTPVerifyPublicForm(initial=initial)
        context["phone"] = phone
        return context

    def post(self, request, *args, **kwargs):
        from apps.accounts.services import OTPDeliveryError, OTPRateLimitError, OTPService
        from core.audit import create_audit_log

        action = str(request.POST.get("action", "verify")).strip().lower()
        phone = self._resolve_phone()
        user = User.objects.filter(phone=phone, is_active=True).first() if phone else None
        if not user:
            messages.error(request, "Invalid phone number for verification.")
            return self.render_to_response(self.get_context_data())

        if action == "resend":
            try:
                otp_token, plain_code = OTPService.generate_otp(
                    phone=user.phone,
                    user=user,
                    purpose=OTPPurpose.VERIFY_PHONE,
                    delivery_method="sms",
                )
                OTPService.send_otp(user.phone, otp_token, plain_code, user)
            except (OTPRateLimitError, OTPDeliveryError) as exc:
                messages.error(request, str(exc))
                return self.render_to_response(self.get_context_data())
            messages.success(request, "A new OTP has been sent.")
            return self.render_to_response(self.get_context_data())

        form = OTPVerifyPublicForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Enter a valid OTP code.")
            return self.render_to_response(self.get_context_data(form=form))

        verified, message = OTPService.verify_otp(
            phone=user.phone,
            code=form.cleaned_data["code"],
            purpose=OTPPurpose.VERIFY_PHONE,
            user=user,
        )
        if not verified:
            messages.error(request, message)
            return self.render_to_response(self.get_context_data(form=form))

        user.phone_verified = True
        user.phone_verified_at = timezone.now()
        user.save(update_fields=["phone_verified", "phone_verified_at"])
        create_audit_log(
            actor=user,
            action="phone_verified",
            entity_type="User",
            entity_id=user.id,
            metadata={"source": "frontend_otp"},
        )

        login(request, user)
        request.session.pop("pending_verification_phone", None)
        messages.success(request, "Phone verified successfully.")
        return redirect("chama:join_chama")


class ForgotPasswordView(TemplateView):
    template_name = "auth/forgot_password.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = PasswordResetRequestForm()
        return context

    def post(self, request, *args, **kwargs):
        form = PasswordResetRequestForm(request.POST)
        if form.is_valid():
            phone = form.cleaned_data["phone"]
            user = User.objects.filter(phone=phone, is_active=True).first()
            if user:
                raw_token = secrets.token_urlsafe(32)
                ttl_minutes = int(getattr(settings, "PASSWORD_RESET_TOKEN_MINUTES", 30))
                expires_at = timezone.now() + timedelta(minutes=ttl_minutes)
                PasswordResetToken.objects.create(
                    user=user,
                    token_hash=PasswordResetToken.hash_token(raw_token),
                    expires_at=expires_at,
                )

                # Only expose tokenized URL in DEBUG/testing environments.
                if settings.DEBUG:
                    reset_url = request.build_absolute_uri(
                        f"{reverse('auth:reset_password')}?token={raw_token}"
                    )
                    messages.info(request, f"Dev reset link: {reset_url}")

            messages.success(
                request,
                "If the account exists, password reset instructions have been sent.",
            )
            return redirect("auth:forgot_password")

        messages.error(request, "Please enter a valid phone number.")

        context = self.get_context_data()
        context["form"] = form
        return self.render_to_response(context)


class ResetPasswordView(TemplateView):
    template_name = "auth/reset_password.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        initial = {}
        token = str(self.request.GET.get("token", "")).strip()
        if token:
            initial["token"] = token
        context["form"] = ResetPasswordConfirmForm(initial=initial)
        return context

    def post(self, request, *args, **kwargs):
        form = ResetPasswordConfirmForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Your password has been reset successfully.")
            return redirect("auth:login")
        else:
            messages.error(request, "Please correct the errors below.")

        context = self.get_context_data()
        context["form"] = form
        return self.render_to_response(context)


@login_required
def change_password_view(request):
    if request.method == "POST":
        form = ChangePasswordForm(request.POST, user=request.user)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "Your password has been changed successfully.")
            return redirect("auth:profile")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = ChangePasswordForm(user=request.user)

    return render(request, "auth/change_password.html", {"form": form})


@login_required
def profile_view(request):
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Your profile has been updated successfully.")
            return redirect("auth:profile")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = ProfileForm(instance=request.user)

    return render(request, "auth/profile.html", {"form": form})


# Function-based view for backward compatibility
def login_view(request):
    view = LoginView.as_view()
    return view(request)


def register_view(request):
    view = RegisterView.as_view()
    return view(request)


def verify_phone_otp_view(request):
    return VerifyPhoneOTPView.as_view()(request)


def forgot_password_view(request):
    return ForgotPasswordView.as_view()(request)


def reset_password_view(request):
    return ResetPasswordView.as_view()(request)


def home_view(request):
    if request.user.is_authenticated:
        return redirect(resolve_dashboard_route_for_request(request))
    else:
        return redirect("auth:login")


class TermsOfServiceView(TemplateView):
    template_name = "auth/terms_of_service.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Terms of Service"
        return context


class PrivacyPolicyView(TemplateView):
    template_name = "auth/privacy_policy.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Privacy Policy"
        return context


def terms_of_service_view(request):
    return TermsOfServiceView.as_view()(request)


def privacy_policy_view(request):
    return PrivacyPolicyView.as_view()(request)


@login_required
@csrf_protect
def switch_chama_view(request):
    if request.method != "POST":
        return redirect(resolve_dashboard_route_for_request(request))

    chama_id = str(request.POST.get("chama_id", "")).strip()
    if not chama_id:
        messages.error(request, "Select a chama to switch.")
        return redirect(
            request.META.get(
                "HTTP_REFERER", resolve_dashboard_route_for_request(request)
            )
        )

    membership = Membership.objects.filter(
        user=request.user,
        chama_id=chama_id,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    ).first()
    if not membership:
        messages.error(request, "You are not an approved active member in that chama.")
        return redirect(
            request.META.get(
                "HTTP_REFERER", resolve_dashboard_route_for_request(request)
            )
        )

    request.session["active_chama_id"] = str(membership.chama_id)
    messages.success(request, f"Switched to {membership.chama.name}.")
    return redirect(
        request.META.get("HTTP_REFERER", resolve_dashboard_route_for_request(request))
    )


@login_required
def security_center_view(request):
    scoped_chama_id = request.GET.get("chama_id") or request.session.get(
        "active_chama_id"
    )
    memberships = Membership.objects.select_related("chama").filter(
        user=request.user,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    )
    active_membership = memberships.filter(chama_id=scoped_chama_id).first()
    if active_membership is None:
        active_membership = memberships.first()

    sessions = (
        DeviceSession.objects.filter(user=request.user)
        .select_related("chama")
        .order_by("-last_seen")[:20]
    )
    login_attempts = LoginAttempt.objects.filter(user=request.user).order_by(
        "-created_at"
    )[:20]
    context = {
        "title": "Security Center",
        "active_membership": active_membership,
        "sessions": sessions,
        "login_attempts": login_attempts,
    }
    return render(request, "auth/security_center.html", context)


@login_required
def logout_view(request):
    django_logout(request)
    messages.success(request, "You have been signed out successfully.")
    return redirect("auth:login")
