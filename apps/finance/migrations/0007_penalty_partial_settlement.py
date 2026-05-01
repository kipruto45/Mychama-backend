from decimal import Decimal

from django.db import migrations, models
from django.db.models import F, Q


def seed_paid_penalty_amounts(apps, schema_editor):
    Penalty = apps.get_model("finance", "Penalty")
    Penalty.objects.filter(status="paid").update(amount_paid=F("amount"))


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0006_expense_workflow_hardening"),
    ]

    operations = [
        migrations.AddField(
            model_name="penalty",
            name="amount_paid",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12),
        ),
        migrations.RunPython(seed_paid_penalty_amounts, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="penalty",
            constraint=models.CheckConstraint(
                condition=Q(amount_paid__gte=Decimal("0.00")),
                name="penalty_amount_paid_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="penalty",
            constraint=models.CheckConstraint(
                condition=Q(amount_paid__lte=F("amount")),
                name="penalty_amount_paid_lte_amount",
            ),
        ),
    ]
