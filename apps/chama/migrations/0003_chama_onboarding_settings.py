import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chama", "0002_secure_invites"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="chama",
            name="privacy",
            field=models.CharField(
                choices=[
                    ("private", "Private"),
                    ("invite_only", "Invite Only"),
                    ("open", "Open"),
                ],
                default="invite_only",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="invite",
            name="code",
            field=models.CharField(blank=True, db_index=True, max_length=12, unique=True),
        ),
        migrations.CreateModel(
            name="ChamaContributionSetting",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("contribution_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                (
                    "contribution_frequency",
                    models.CharField(
                        choices=[
                            ("daily", "Daily"),
                            ("weekly", "Weekly"),
                            ("biweekly", "Bi-weekly"),
                            ("monthly", "Monthly"),
                        ],
                        default="monthly",
                        max_length=20,
                    ),
                ),
                ("due_day", models.PositiveIntegerField(default=1)),
                ("grace_period_days", models.PositiveIntegerField(default=0)),
                ("late_fine_amount", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                (
                    "chama",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="contribution_setting",
                        to="chama.chama",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [models.Index(fields=["contribution_frequency", "due_day"], name="chama_contr_contrib_b3165c_idx")],
            },
        ),
        migrations.CreateModel(
            name="ChamaFinanceSetting",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "currency",
                    models.CharField(
                        choices=[
                            ("KES", "Kenyan Shilling"),
                            ("USD", "US Dollar"),
                            ("EUR", "Euro"),
                            ("GBP", "British Pound"),
                        ],
                        default="KES",
                        max_length=3,
                    ),
                ),
                ("payment_methods", models.JSONField(blank=True, default=list)),
                ("loans_enabled", models.BooleanField(default=True)),
                ("fines_enabled", models.BooleanField(default=True)),
                ("approval_rule", models.CharField(default="maker_checker", max_length=50)),
                (
                    "chama",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="finance_setting",
                        to="chama.chama",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ChamaMeetingSetting",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "meeting_frequency",
                    models.CharField(
                        choices=[
                            ("weekly", "Weekly"),
                            ("biweekly", "Bi-weekly"),
                            ("monthly", "Monthly"),
                            ("quarterly", "Quarterly"),
                        ],
                        default="monthly",
                        max_length=20,
                    ),
                ),
                ("quorum_percentage", models.PositiveIntegerField(default=50)),
                ("voting_enabled", models.BooleanField(default=True)),
                (
                    "chama",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="meeting_setting",
                        to="chama.chama",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="chamameetingsetting",
            constraint=models.CheckConstraint(
                condition=models.Q(("quorum_percentage__gte", 1), ("quorum_percentage__lte", 100)),
                name="meeting_quorum_between_1_100",
            ),
        ),
        migrations.CreateModel(
            name="ChamaNotificationSetting",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("member_join_alerts", models.BooleanField(default=True)),
                ("payment_received_alerts", models.BooleanField(default=True)),
                ("meeting_reminders", models.BooleanField(default=True)),
                ("loan_updates", models.BooleanField(default=True)),
                (
                    "chama",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notification_setting",
                        to="chama.chama",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
