from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import timezone


@receiver(user_logged_in)
def update_last_login_metadata(sender, request, user, **kwargs):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR") if request else None
    ip_address = forwarded_for.split(",")[0].strip() if forwarded_for else None
    if not ip_address and request:
        ip_address = request.META.get("REMOTE_ADDR")

    user.last_login_at = timezone.now()
    if ip_address:
        user.last_login_ip = ip_address
    user.save(update_fields=["last_login_at", "last_login_ip"])
