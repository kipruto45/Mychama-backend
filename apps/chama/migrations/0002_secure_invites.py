import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chama", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="invite",
            name="invitee_phone",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="invite",
            name="invitee_email",
            field=models.EmailField(blank=True, default="", max_length=254),
        ),
        migrations.AddField(
            model_name="invite",
            name="invitee_user",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="received_chama_invites", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="invite",
            name="role_to_assign",
            field=models.CharField(blank=True, choices=[("SUPERADMIN", "Super Admin"), ("ADMIN", "Admin"), ("CHAMA_ADMIN", "Chama Admin"), ("TREASURER", "Treasurer"), ("SECRETARY", "Secretary"), ("MEMBER", "Member"), ("AUDITOR", "Auditor")], default="MEMBER", max_length=20),
        ),
        migrations.AddField(
            model_name="invite",
            name="declined_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="invite",
            name="revoked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="invite",
            name="revoke_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="invite",
            name="revoked_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="revoked_chama_invites", to=settings.AUTH_USER_MODEL),
        ),
    ]
