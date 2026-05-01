import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("governance", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Motion",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                (
                    "status",
                    models.CharField(
                        choices=[("open", "Open"), ("closed", "Closed"), ("cancelled", "Cancelled")],
                        default="open",
                        max_length=20,
                    ),
                ),
                ("start_time", models.DateTimeField(default=django.utils.timezone.now)),
                ("end_time", models.DateTimeField()),
                ("quorum_percent", models.PositiveSmallIntegerField(default=50)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("eligible_roles", models.JSONField(blank=True, default=list)),
                (
                    "chama",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="motions",
                        to="chama.chama",
                    ),
                ),
                (
                    "closed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="closed_motions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_motions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="governance_motion_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="MotionVote",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "vote",
                    models.CharField(
                        choices=[("yes", "Yes"), ("no", "No"), ("abstain", "Abstain")],
                        max_length=10,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="governance_motionvote_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="governance_motionvote_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "motion",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="votes",
                        to="governance.motion",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="motion_votes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="MotionResult",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("total_votes", models.PositiveIntegerField(default=0)),
                ("yes_votes", models.PositiveIntegerField(default=0)),
                ("no_votes", models.PositiveIntegerField(default=0)),
                ("abstain_votes", models.PositiveIntegerField(default=0)),
                ("eligible_voters", models.PositiveIntegerField(default=0)),
                ("quorum_met", models.BooleanField(default=False)),
                ("passed", models.BooleanField(default=False)),
                ("calculated_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="governance_motionresult_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="governance_motionresult_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "motion",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="result",
                        to="governance.motion",
                    ),
                ),
            ],
            options={
                "ordering": ["-calculated_at"],
            },
        ),
        migrations.AddIndex(
            model_name="motion",
            index=models.Index(fields=["chama", "status", "end_time"], name="governance_motion_chama_i_8d027e_idx"),
        ),
        migrations.AddIndex(
            model_name="motion",
            index=models.Index(fields=["chama", "created_at"], name="governance_motion_chama_i_0b1d67_idx"),
        ),
        migrations.AddConstraint(
            model_name="motion",
            constraint=models.CheckConstraint(
                condition=models.Q(quorum_percent__gte=1) & models.Q(quorum_percent__lte=100),
                name="governance_motion_quorum_between_1_100",
            ),
        ),
        migrations.AddConstraint(
            model_name="motionvote",
            constraint=models.UniqueConstraint(
                fields=("motion", "user"),
                name="unique_motion_vote_per_user",
            ),
        ),
        migrations.AddIndex(
            model_name="motionvote",
            index=models.Index(fields=["motion", "vote"], name="governance_motionv_motion__6b447f_idx"),
        ),
        migrations.AddIndex(
            model_name="motionvote",
            index=models.Index(fields=["user", "created_at"], name="governance_motionv_user_id_caf7cb_idx"),
        ),
    ]
