from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chama", "0003_chama_onboarding_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="loanpolicy",
            name="approval_rules",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="allow_offset_from_savings",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="allow_restructure",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="block_defaulted_loans",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="committee_threshold_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Amounts at or above this threshold require committee approval when enabled.",
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="default_after_days_overdue",
            field=models.PositiveIntegerField(default=30),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="grace_period_days",
            field=models.PositiveIntegerField(default=3),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="guarantor_capacity_multiplier",
            field=models.DecimalField(
                decimal_places=2,
                default=1.0,
                help_text="Guarantor capacity = total contributions x multiplier minus open guarantee exposure.",
                max_digits=5,
            ),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="late_fee_type",
            field=models.CharField(default="percentage", max_length=20),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="late_fee_value",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="max_member_loan_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Absolute per-member loan cap. Zero means use product/policy derived caps only.",
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="medium_loan_guarantors_count",
            field=models.PositiveIntegerField(default=2),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="medium_loan_threshold",
            field=models.DecimalField(decimal_places=2, default=50000, max_digits=12),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="min_contribution_compliance_percent",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="min_membership_days",
            field=models.PositiveIntegerField(default=90),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="minimum_credit_score",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="require_email_verification",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="require_kyc",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="require_loan_purpose",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="require_no_overdue_contributions",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="require_phone_verification",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="reserve_liquidity_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Minimum cash reserve the chama must retain after loan disbursement.",
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name="loanpolicy",
            name="restrict_member_privileges_on_default",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="loanpolicy",
            name="loan_cap_multiplier",
            field=models.DecimalField(
                decimal_places=2,
                default=2.0,
                help_text="Max loan = this x total contributions",
                max_digits=5,
            ),
        ),
    ]
