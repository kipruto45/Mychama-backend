from django.db import migrations, models
from django.db.models.functions import Lower


class Migration(migrations.Migration):
    dependencies = [
        ("chama", "0007_loanpolicy_block_pending_loan_applications_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="invite",
            index=models.Index(Lower("code"), name="chama_invite_code_lower_idx"),
        ),
    ]

