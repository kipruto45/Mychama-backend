import uuid
from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0005_manual_policy_statement_imports"),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentSettlement",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("payment_method", models.CharField(choices=[("mpesa", "M-Pesa"), ("card", "Card"), ("cash", "Cash"), ("bank", "Bank Transfer")], max_length=20)),
                ("provider_name", models.CharField(blank=True, max_length=50)),
                ("settlement_reference", models.CharField(db_index=True, max_length=150, unique=True)),
                ("settlement_date", models.DateField(default=django.utils.timezone.localdate)),
                ("currency", models.CharField(choices=[("KES", "Kenyan Shilling"), ("USD", "US Dollar"), ("EUR", "Euro"), ("GBP", "British Pound")], default="KES", max_length=3)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("posted", "Posted"), ("reconciled", "Reconciled"), ("cancelled", "Cancelled")], default="pending", max_length=20)),
                ("gross_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("fee_amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("net_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("clearing_account_key", models.CharField(default="mpesa_clearing", max_length=50)),
                ("destination_account_key", models.CharField(default="bank_account", max_length=50)),
                ("fee_account_key", models.CharField(default="payment_processing_fees", max_length=50)),
                ("posted_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_paymentsettlement_created", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_paymentsettlement_updated", to=settings.AUTH_USER_MODEL)),
                ("chama", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payment_settlements", to="chama.chama")),
                ("journal_entry", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payment_settlements", to="finance.journalentry")),
                ("posted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="posted_payment_settlements", to=settings.AUTH_USER_MODEL)),
                ("statement_import", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="settlements", to="payments.paymentstatementimport")),
            ],
            options={
                "ordering": ["-settlement_date", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="PaymentSettlementAllocation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("settled_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_paymentsettlementallocation_created", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_paymentsettlementallocation_updated", to=settings.AUTH_USER_MODEL)),
                ("payment_transaction", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="settlement_allocations", to="payments.paymenttransaction")),
                ("settlement", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="allocations", to="payments.paymentsettlement")),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="paymentsettlement",
            constraint=models.CheckConstraint(condition=models.Q(("gross_amount__gt", Decimal("0.00"))), name="payment_settlement_gross_positive"),
        ),
        migrations.AddConstraint(
            model_name="paymentsettlement",
            constraint=models.CheckConstraint(condition=models.Q(("fee_amount__gte", Decimal("0.00"))), name="payment_settlement_fee_non_negative"),
        ),
        migrations.AddConstraint(
            model_name="paymentsettlement",
            constraint=models.CheckConstraint(condition=models.Q(("net_amount__gte", Decimal("0.00"))), name="payment_settlement_net_non_negative"),
        ),
        migrations.AddConstraint(
            model_name="paymentsettlementallocation",
            constraint=models.UniqueConstraint(fields=("settlement", "payment_transaction"), name="uniq_settlement_transaction_allocation"),
        ),
        migrations.AddConstraint(
            model_name="paymentsettlementallocation",
            constraint=models.CheckConstraint(condition=models.Q(("settled_amount__gt", Decimal("0.00"))), name="payment_settlement_allocation_amount_positive"),
        ),
        migrations.AddIndex(
            model_name="paymentsettlement",
            index=models.Index(fields=["chama", "payment_method", "status"], name="payments_pay_chama_i_906f29_idx"),
        ),
        migrations.AddIndex(
            model_name="paymentsettlement",
            index=models.Index(fields=["provider_name", "status"], name="payments_pay_provider_496567_idx"),
        ),
        migrations.AddIndex(
            model_name="paymentsettlement",
            index=models.Index(fields=["settlement_date", "status"], name="payments_pay_settlem_5664b6_idx"),
        ),
        migrations.AddIndex(
            model_name="paymentsettlementallocation",
            index=models.Index(fields=["settlement", "created_at"], name="payments_pay_settlem_ef1725_idx"),
        ),
        migrations.AddIndex(
            model_name="paymentsettlementallocation",
            index=models.Index(fields=["payment_transaction", "created_at"], name="payments_pay_payment_16d098_idx"),
        ),
    ]
