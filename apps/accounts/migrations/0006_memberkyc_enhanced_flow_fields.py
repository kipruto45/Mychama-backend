from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_alter_user_password_changed_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="memberkyc",
            name="blacklist_match",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="document_type",
            field=models.CharField(
                choices=[
                    ("national_id", "National ID"),
                    ("passport", "Passport"),
                    ("alien_id", "Alien ID"),
                    ("military_id", "Military ID"),
                ],
                default="national_id",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="duplicate_id_detected",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="id_back_image",
            field=models.FileField(blank=True, upload_to="kyc/id_back/"),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="kyc_tier",
            field=models.CharField(
                choices=[
                    ("tier_0", "Tier 0 Unverified"),
                    ("tier_1", "Tier 1 Basic KYC"),
                    ("tier_2", "Tier 2 Full KYC"),
                    ("tier_3", "Tier 3 Enhanced KYC"),
                ],
                default="tier_0",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="pep_match",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="sanctions_match",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="verification_score",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddIndex(
            model_name="memberkyc",
            index=models.Index(
                fields=["id_number", "document_type"],
                name="accounts_me_id_numb_a41117_idx",
            ),
        ),
    ]
