import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0003_card_payment_models"),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentReconciliationCase",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("mismatch_type", models.CharField(choices=[("provider_verification_mismatch", "Provider Verification Mismatch"), ("duplicate_provider_reference", "Duplicate Provider Reference"), ("orphan_webhook", "Orphan Webhook"), ("callback_missing", "Callback Missing"), ("webhook_processing_error", "Webhook Processing Error"), ("manual_review", "Manual Review")], max_length=50)),
                ("case_status", models.CharField(choices=[("open", "Open"), ("in_review", "In Review"), ("resolved", "Resolved"), ("dismissed", "Dismissed")], default="open", max_length=20)),
                ("expected_amount", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("received_amount", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("expected_reference", models.CharField(blank=True, max_length=255)),
                ("received_reference", models.CharField(blank=True, max_length=255)),
                ("resolution_notes", models.TextField(blank=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("assigned_to", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="assigned_payment_reconciliation_cases", to=settings.AUTH_USER_MODEL)),
                ("chama", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payment_reconciliation_cases", to="chama.chama")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_paymentreconciliationcase_created", to=settings.AUTH_USER_MODEL)),
                ("payment_intent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="reconciliation_cases", to="payments.paymentintent")),
                ("payment_transaction", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reconciliation_cases", to="payments.paymenttransaction")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_paymentreconciliationcase_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="paymentreconciliationcase",
            index=models.Index(fields=["chama", "case_status", "mismatch_type"], name="payments_prc_chama_status_type_idx"),
        ),
        migrations.AddIndex(
            model_name="paymentreconciliationcase",
            index=models.Index(fields=["payment_intent", "case_status"], name="payments_prc_intent_status_idx"),
        ),
        migrations.AddIndex(
            model_name="paymentreconciliationcase",
            index=models.Index(fields=["payment_transaction", "case_status"], name="payments_prc_tx_status_idx"),
        ),
        migrations.AddIndex(
            model_name="paymentreconciliationcase",
            index=models.Index(fields=["assigned_to", "case_status"], name="payments_prc_assignee_status_idx"),
        ),
    ]
