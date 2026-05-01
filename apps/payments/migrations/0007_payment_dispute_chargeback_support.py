from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0006_payment_settlement_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentdispute",
            name="amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="paymentdispute",
            name="provider_case_reference",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="paymentdispute",
            name="financial_reversal_entry",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payment_disputes", to="finance.ledgerentry"),
        ),
        migrations.AddField(
            model_name="paymentdispute",
            name="metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name="paymentdispute",
            name="category",
            field=models.CharField(choices=[("duplicate", "Duplicate Charge"), ("incorrect_amount", "Incorrect Amount"), ("failed_callback", "Failed Callback"), ("missing_reference", "Missing Reference"), ("fraud", "Fraud"), ("chargeback", "Chargeback"), ("provider_dispute", "Provider Dispute"), ("other", "Other")], default="other", max_length=30),
        ),
        migrations.AlterField(
            model_name="paymentdispute",
            name="status",
            field=models.CharField(choices=[("OPEN", "Open"), ("IN_REVIEW", "In Review"), ("RESOLVED", "Resolved"), ("REJECTED", "Rejected"), ("WON", "Won"), ("LOST", "Lost")], default="OPEN", max_length=20),
        ),
        migrations.AddConstraint(
            model_name="paymentdispute",
            constraint=models.CheckConstraint(
                condition=models.Q(amount__isnull=True) | models.Q(amount__gt=Decimal("0.00")),
                name="payment_dispute_amount_positive_or_null",
            ),
        ),
        migrations.AddIndex(
            model_name="paymentdispute",
            index=models.Index(fields=["provider_case_reference"], name="payments_pay_provider_bf43c7_idx"),
        ),
    ]
