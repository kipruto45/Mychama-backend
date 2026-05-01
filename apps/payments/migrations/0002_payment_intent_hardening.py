import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentintent",
            name="user",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payment_intents", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="paymentintent",
            name="checkout_request_id",
            field=models.CharField(blank=True, db_index=True, max_length=120),
        ),
        migrations.AddField(
            model_name="paymentintent",
            name="merchant_request_id",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="paymentintent",
            name="mpesa_receipt_number",
            field=models.CharField(blank=True, db_index=True, max_length=80),
        ),
        migrations.AddField(
            model_name="paymentintent",
            name="failure_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="paymentintent",
            name="raw_response",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name="PaymentTransaction",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("provider", models.CharField(default="mpesa", max_length=20)),
                ("reference", models.CharField(max_length=120, unique=True)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("status", models.CharField(choices=[("INITIATED", "Initiated"), ("PENDING", "Pending"), ("SUCCESS", "Success"), ("FAILED", "Failed"), ("EXPIRED", "Expired"), ("CANCELLED", "Cancelled")], max_length=20)),
                ("provider_response", models.JSONField(blank=True, default=dict)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
                ("payment_intent", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="transactions", to="payments.paymentintent")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="paymenttransaction",
            constraint=models.CheckConstraint(condition=models.Q(("amount__gt", Decimal("0.00"))), name="payment_transaction_amount_positive"),
        ),
        migrations.AddIndex(model_name="paymenttransaction", index=models.Index(fields=["payment_intent", "status", "created_at"], name="payments_pa_paymen_9c8c7b_idx")),
        migrations.AddIndex(model_name="paymenttransaction", index=models.Index(fields=["provider", "status"], name="payments_pa_provide_8094be_idx")),
    ]
