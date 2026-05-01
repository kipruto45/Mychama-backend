from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chama", "0004_loan_policy_hardening"),
    ]

    operations = [
        migrations.AddField(
            model_name="loanpolicy",
            name="notify_guarantors_on_overdue",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="recovery_review_after_days_overdue",
            field=models.PositiveIntegerField(default=14),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="restrict_invites_on_default",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="restrict_new_loans_on_overdue",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="restrict_voting_on_default",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="restrict_withdrawals_on_default",
            field=models.BooleanField(default=False),
        ),
    ]
