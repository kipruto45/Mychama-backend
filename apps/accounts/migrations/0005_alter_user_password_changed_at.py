from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_memberkyc_automation_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="password_changed_at",
            field=models.DateTimeField(blank=True, default=timezone.now, null=True),
        ),
    ]
