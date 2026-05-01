from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_memberkyc_location_latitude_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="memberkyc",
            name="auto_verification_provider",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="auto_verification_reference",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="auto_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="escalated_to_system_admin_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="last_rejection_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="last_submitted_at",
            field=models.DateTimeField(auto_now_add=True, null=True),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="rejection_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="resubmission_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="submission_attempts",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddIndex(
            model_name="memberkyc",
            index=models.Index(fields=["status", "last_submitted_at"], name="accounts_me_status_2d6f51_idx"),
        ),
        migrations.AddIndex(
            model_name="memberkyc",
            index=models.Index(fields=["rejection_attempts", "status"], name="accounts_me_rejecti_4f3182_idx"),
        ),
    ]
