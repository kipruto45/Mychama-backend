import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("security", "0003_accountlockout_deviceloginattempt_securityevent_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="RefreshTokenRecord",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("family_id", models.UUIDField(db_index=True)),
                ("jti", models.CharField(db_index=True, max_length=255, unique=True)),
                ("parent_jti", models.CharField(blank=True, default="", max_length=255)),
                ("device_name", models.CharField(blank=True, default="", max_length=255)),
                ("device_id", models.CharField(blank=True, default="", max_length=255)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.TextField(blank=True, default="")),
                ("issued_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("used_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("revoked_reason", models.CharField(blank=True, default="", max_length=64)),
                ("reuse_detected_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="refresh_token_records",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-issued_at"],
            },
        ),
        migrations.AddIndex(
            model_name="refreshtokenrecord",
            index=models.Index(fields=["user", "family_id"], name="security_re_user_id_432c1f_idx"),
        ),
        migrations.AddIndex(
            model_name="refreshtokenrecord",
            index=models.Index(fields=["family_id", "revoked_at"], name="security_re_family__db13d1_idx"),
        ),
        migrations.AddIndex(
            model_name="refreshtokenrecord",
            index=models.Index(fields=["user", "revoked_at"], name="security_re_user_id_0d2f73_idx"),
        ),
        migrations.AddIndex(
            model_name="refreshtokenrecord",
            index=models.Index(fields=["expires_at"], name="security_re_expires_348bb8_idx"),
        ),
    ]
