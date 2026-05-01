from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_user_access_and_kyc_events"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="kycevent",
            new_name="accounts_ky_kyc_rec_643129_idx",
            old_name="accounts_ky_kyc_rec_307215_idx",
        ),
        migrations.RenameIndex(
            model_name="kycevent",
            new_name="accounts_ky_user_id_9ee97a_idx",
            old_name="accounts_ky_user_id_5fd383_idx",
        ),
        migrations.RenameIndex(
            model_name="kycevent",
            new_name="accounts_ky_event_t_862b8d_idx",
            old_name="accounts_ky_event_t_2cdf6f_idx",
        ),
        migrations.RenameIndex(
            model_name="kycevent",
            new_name="accounts_ky_code_4d65c9_idx",
            old_name="accounts_ky_code_3d6e58_idx",
        ),
        migrations.RenameIndex(
            model_name="memberkyc",
            new_name="accounts_me_provide_3697ae_idx",
            old_name="accounts_me_provider_6cd97b_idx",
        ),
        migrations.RenameIndex(
            model_name="memberkyc",
            new_name="accounts_me_confide_8a7280_idx",
            old_name="accounts_me_confide_cc73a2_idx",
        ),
        migrations.RenameIndex(
            model_name="memberkyc",
            new_name="accounts_me_require_a23ac7_idx",
            old_name="accounts_me_require_510f32_idx",
        ),
        migrations.RenameIndex(
            model_name="user",
            new_name="accounts_us_otp_ver_d898db_idx",
            old_name="accounts_us_otp_ver_eb1fb0_idx",
        ),
        migrations.RenameIndex(
            model_name="user",
            new_name="accounts_us_tier_ac_f894a0_idx",
            old_name="accounts_us_tier_ac_a8b3f8_idx",
        ),
        migrations.RenameIndex(
            model_name="user",
            new_name="accounts_us_kyc_sta_e3d46c_idx",
            old_name="accounts_us_kyc_sta_48eb68_idx",
        ),
        migrations.RenameIndex(
            model_name="user",
            new_name="accounts_us_account_180e04_idx",
            old_name="accounts_us_account_7626ec_idx",
        ),
        migrations.AlterField(
            model_name="memberkyc",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("pending", "Pending"),
                    ("queued", "Queued"),
                    ("processing", "Processing"),
                    ("under_review", "Under Review"),
                    ("resubmit_required", "Resubmit Required"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("frozen", "Frozen"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
