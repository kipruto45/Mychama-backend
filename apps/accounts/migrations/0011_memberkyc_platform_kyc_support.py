from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        (
            "accounts",
            "0010_rename_accounts_ot_ident_purpose_used_idx_accounts_ot_identif_be8f2a_idx_and_more",
        ),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="memberkyc",
            name="uniq_member_kyc_per_user_chama",
        ),
        migrations.AlterField(
            model_name="memberkyc",
            name="chama",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="kyc_records",
                to="chama.chama",
            ),
        ),
        migrations.AddConstraint(
            model_name="memberkyc",
            constraint=models.UniqueConstraint(
                condition=models.Q(chama__isnull=False),
                fields=("user", "chama"),
                name="uniq_member_kyc_per_user_chama",
            ),
        ),
        migrations.AddConstraint(
            model_name="memberkyc",
            constraint=models.UniqueConstraint(
                condition=models.Q(chama__isnull=True),
                fields=("user",),
                name="uniq_platform_kyc_per_user",
            ),
        ),
    ]
