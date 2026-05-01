from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.conf import settings


def _run_task_inline() -> bool:
    return bool(
        getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
    )


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


@receiver(post_save, sender='accounts.User')
def send_welcome_email(sender, instance, created, **kwargs):
    """Send a welcome email to new users after registration."""
    if not created or not instance.email:
        return
    
    # Avoid sending email if this is a test/debug mode and flag is set
    if getattr(settings, 'SKIP_WELCOME_EMAIL_IN_DEBUG', False) and settings.DEBUG:
        return

    try:
        from apps.accounts.tasks import send_welcome_email_task

        if _run_task_inline():
            send_welcome_email_task(user_id=str(instance.id))
        else:
            send_welcome_email_task.delay(user_id=str(instance.id))
    except Exception:
        # Fallback to synchronous sending if celery not available
        _send_welcome_email_sync(instance)


def _send_welcome_email_sync(user):
    """Synchronously send welcome email (fallback if celery unavailable)."""
    try:
        from apps.notifications.email import send_email_message

        # Build email context
        context = {
            'user_name': user.get_display_name(),
            'app_url': getattr(settings, 'FRONTEND_URL', 'https://my-cham-a.app'),
            'dashboard_url': f"{getattr(settings, 'FRONTEND_URL', 'https://my-cham-a.app')}/dashboard",
            'logo_url': f"{getattr(settings, 'FRONTEND_URL', 'https://my-cham-a.app')}/logo.png",
        }
        
        # Load and render HTML template
        html_body = _render_welcome_email(context)
        
        send_email_message(
            subject="Welcome to MyChama – Your Community Savings Journey Starts Here",
            recipient_list=[user.email],
            body="Welcome to MyChama!",  # Plain text fallback
            html_body=html_body,
        )
    except Exception as exc:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception(f"Failed to send welcome email to {user.email}", exc_info=exc)


def _render_welcome_email(context):
    """Render the welcome email HTML template with context."""
    from django.template.loader import render_to_string
    
    try:
        # Try to load from emails directory
        html = render_to_string('emails/auth/01-welcome.html', context)
        return html
    except Exception:
        # Fallback: inline minimal HTML template
        return f"""
        <html>
            <body style="font-family: Arial, sans-serif; padding: 20px;">
                <h1>Welcome to MyChama, {context.get('user_name', 'User')}!</h1>
                <p>Thank you for joining MyChama – the modern platform for community savings groups.</p>
                <p>Your journey to financial empowerment and community building starts here.</p>
                <a href="{context.get('dashboard_url')}" style="display: inline-block; padding: 10px 20px; background: #16A34A; color: white; text-decoration: none; border-radius: 5px;">Get Started</a>
                <p style="color: #666; font-size: 12px; margin-top: 20px;">
                    MyChama Team<br/>
                    <a href="{context.get('app_url')}">Visit MyChama</a>
                </p>
            </body>
        </html>
        """
