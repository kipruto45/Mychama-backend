from __future__ import annotations

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def generate_receipt_download_token() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0009_remove_cardpaymentintent_chama_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentReceiptDownloadToken",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="payments_paymentreceiptdownloadtoken_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="payments_paymentreceiptdownloadtoken_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "token",
                    models.CharField(
                        max_length=64,
                        unique=True,
                        default=generate_receipt_download_token,
                        db_index=True,
                    ),
                ),
                ("expires_at", models.DateTimeField()),
                ("consumed_at", models.DateTimeField(null=True, blank=True)),
                ("ip_address", models.GenericIPAddressField(null=True, blank=True)),
                ("user_agent", models.TextField(blank=True, default="")),
                (
                    "payment_intent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="receipt_download_tokens",
                        to="payments.paymentintent",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="receipt_download_tokens",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["payment_intent", "expires_at"], name="payments_prdl_pi_exp_idx"),
                    models.Index(fields=["requested_by", "expires_at"], name="payments_prdl_user_exp_idx"),
                    models.Index(fields=["consumed_at"], name="payments_prdl_consumed_idx"),
                ],
            },
        ),
    ]
