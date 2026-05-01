from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
from django.db.models import F, Q
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0007_penalty_partial_settlement"),
    ]

    operations = [
        migrations.AddField(
            model_name="contribution",
            name="refunded_amount",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12),
        ),
        migrations.AddField(
            model_name="contribution",
            name="refunded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="contribution",
            name="refunded_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="refunded_contributions", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddConstraint(
            model_name="contribution",
            constraint=models.CheckConstraint(
                condition=Q(refunded_amount__gte=Decimal("0.00")),
                name="contribution_refunded_amount_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="contribution",
            constraint=models.CheckConstraint(
                condition=Q(refunded_amount__lte=F("amount")),
                name="contribution_refunded_amount_lte_amount",
            ),
        ),
    ]
