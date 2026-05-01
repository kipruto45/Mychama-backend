from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0010_loanauditlog_loanapplication_approval_requirements_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="wallet",
            name="owner_id",
            field=models.CharField(max_length=64),
        ),
    ]
