from django.db import migrations, models


def backfill_otptoken_identifier(apps, schema_editor):
    OTPToken = apps.get_model("accounts", "OTPToken")
    OTPToken.objects.filter(identifier="").update(identifier=models.F("phone"))


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_memberkyc_account_frozen_for_compliance_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="otptoken",
            name="identifier",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.RunPython(
            backfill_otptoken_identifier,
            migrations.RunPython.noop,
        ),
        migrations.AddIndex(
            model_name="otptoken",
            index=models.Index(
                fields=["identifier", "purpose", "is_used"],
                name="accounts_ot_ident_purpose_used_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="otptoken",
            index=models.Index(
                fields=["identifier", "created_at"],
                name="accounts_ot_ident_created_idx",
            ),
        ),
    ]
