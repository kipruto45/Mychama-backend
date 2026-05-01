import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_memberkyc_platform_kyc_support"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="otp_verified",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="account_locked_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="account_frozen",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="tier_access",
            field=models.CharField(
                choices=[
                    ("unverified", "Unverified"),
                    ("tier_0_view_only", "Tier 0 View Only"),
                    ("tier_2_full", "Tier 2 Full Access"),
                    ("restricted", "Restricted"),
                ],
                default="unverified",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="kyc_status",
            field=models.CharField(
                choices=[
                    ("not_started", "Not Started"),
                    ("pending", "Pending"),
                    ("under_review", "Under Review"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("rekyc_required", "Re-KYC Required"),
                    ("frozen", "Frozen"),
                ],
                default="not_started",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="kyc_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="financial_access_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="provider",
            field=models.CharField(default="smile_identity", max_length=64),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="onboarding_path",
            field=models.CharField(default="existing_member_update", max_length=64),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="legal_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="date_of_birth",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="gender",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="nationality",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="phone_number",
            field=models.CharField(blank=True, max_length=16),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="location_label",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="confidence_score",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="quality_front_passed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="quality_back_passed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="liveness_passed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="face_match_score",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="iprs_match_status",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="retry_allowed",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="submitted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="processed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="rejected_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="last_rekyc_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="review_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="provider_payload",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="memberkyc",
            name="provider_result",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddIndex(
            model_name="user",
            index=models.Index(fields=["otp_verified"], name="accounts_us_otp_ver_eb1fb0_idx"),
        ),
        migrations.AddIndex(
            model_name="user",
            index=models.Index(fields=["tier_access"], name="accounts_us_tier_ac_a8b3f8_idx"),
        ),
        migrations.AddIndex(
            model_name="user",
            index=models.Index(fields=["kyc_status"], name="accounts_us_kyc_sta_48eb68_idx"),
        ),
        migrations.AddIndex(
            model_name="user",
            index=models.Index(fields=["account_frozen"], name="accounts_us_account_7626ec_idx"),
        ),
        migrations.AddIndex(
            model_name="memberkyc",
            index=models.Index(fields=["provider", "status"], name="accounts_me_provider_6cd97b_idx"),
        ),
        migrations.AddIndex(
            model_name="memberkyc",
            index=models.Index(fields=["confidence_score", "status"], name="accounts_me_confide_cc73a2_idx"),
        ),
        migrations.AddIndex(
            model_name="memberkyc",
            index=models.Index(fields=["requires_reverification", "next_reverification_due_at"], name="accounts_me_require_510f32_idx"),
        ),
        migrations.CreateModel(
            name="KYCEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("event_type", models.CharField(max_length=64)),
                ("code", models.CharField(max_length=64)),
                ("message", models.CharField(max_length=255)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="acted_kyc_events", to="accounts.user")),
                ("kyc_record", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="accounts.memberkyc")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="kyc_events", to="accounts.user")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="kycevent",
            index=models.Index(fields=["kyc_record", "created_at"], name="accounts_ky_kyc_rec_307215_idx"),
        ),
        migrations.AddIndex(
            model_name="kycevent",
            index=models.Index(fields=["user", "created_at"], name="accounts_ky_user_id_5fd383_idx"),
        ),
        migrations.AddIndex(
            model_name="kycevent",
            index=models.Index(fields=["event_type", "created_at"], name="accounts_ky_event_t_2cdf6f_idx"),
        ),
        migrations.AddIndex(
            model_name="kycevent",
            index=models.Index(fields=["code", "created_at"], name="accounts_ky_code_3d6e58_idx"),
        ),
    ]
