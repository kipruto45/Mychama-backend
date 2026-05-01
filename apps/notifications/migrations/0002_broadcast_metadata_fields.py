from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="broadcastannouncement",
            name="action_url",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="broadcastannouncement",
            name="metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="broadcastannouncement",
            name="priority",
            field=models.CharField(
                choices=[
                    ("low", "Low"),
                    ("normal", "Normal"),
                    ("high", "High"),
                    ("critical", "Critical"),
                ],
                default="normal",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="broadcastannouncement",
            name="segment",
            field=models.CharField(blank=True, max_length=80),
        ),
    ]
