from django.db import migrations, models
from django.db.models.functions import Lower


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0014_memberkyc_provider_result_encrypted_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="user",
            index=models.Index(Lower("email"), name="accounts_user_email_lower_idx"),
        ),
    ]

