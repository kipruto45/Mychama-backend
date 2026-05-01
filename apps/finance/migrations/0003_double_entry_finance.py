import uuid
from decimal import Decimal

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chama", "0001_initial"),
        ("finance", "0002_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Account",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("code", models.CharField(max_length=40)),
                ("name", models.CharField(max_length=120)),
                ("type", models.CharField(choices=[("asset", "Asset"), ("liability", "Liability"), ("equity", "Equity"), ("income", "Income"), ("expense", "Expense")], max_length=20)),
                ("is_active", models.BooleanField(default=True)),
                ("system_managed", models.BooleanField(default=False)),
                ("meta", models.JSONField(blank=True, default=dict)),
                ("chama", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="accounts", to="chama.chama")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["type", "code", "name"]},
        ),
        migrations.CreateModel(
            name="JournalEntry",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("reference", models.CharField(db_index=True, max_length=100)),
                ("description", models.TextField()),
                ("source_type", models.CharField(choices=[("contribution", "Contribution"), ("expense", "Expense"), ("loan", "Loan"), ("loan_repayment", "Loan Repayment"), ("penalty", "Penalty"), ("adjustment", "Adjustment"), ("payment", "Payment"), ("snapshot", "Snapshot")], default="adjustment", max_length=30)),
                ("source_id", models.UUIDField(blank=True, null=True)),
                ("posted_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("idempotency_key", models.CharField(max_length=100)),
                ("is_reversal", models.BooleanField(default=False)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("chama", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="journal_entries", to="chama.chama")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_journal_entries", to=settings.AUTH_USER_MODEL)),
                ("reversal_of", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="reversal_entries", to="finance.journalentry")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-posted_at", "-created_at"]},
        ),
        migrations.CreateModel(
            name="FinancialSnapshot",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("snapshot_date", models.DateField(db_index=True)),
                ("total_balance", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("total_contributions", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("total_loans", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("total_expenses", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("chama", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="financial_snapshots", to="chama.chama")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-snapshot_date", "-created_at"]},
        ),
        migrations.CreateModel(
            name="Expense",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("description", models.CharField(max_length=255)),
                ("category", models.CharField(blank=True, max_length=80)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("expense_date", models.DateField(default=django.utils.timezone.localdate)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("paid", "Paid"), ("reversed", "Reversed")], default="paid", max_length=20)),
                ("vendor_name", models.CharField(blank=True, max_length=120)),
                ("receipt_reference", models.CharField(blank=True, max_length=120)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_expenses", to=settings.AUTH_USER_MODEL)),
                ("chama", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="expenses", to="chama.chama")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
                ("journal_entry", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="expense_record", to="finance.journalentry")),
                ("paid_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="paid_expenses", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-expense_date", "-created_at"]},
        ),
        migrations.AddField(
            model_name="ledgerentry",
            name="journal_entry",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="lines", to="finance.journalentry"),
        ),
        migrations.AddField(
            model_name="ledgerentry",
            name="account",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="ledger_entries", to="finance.account"),
        ),
        migrations.AddField(
            model_name="ledgerentry",
            name="debit",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14),
        ),
        migrations.AddField(
            model_name="ledgerentry",
            name="credit",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14),
        ),
        migrations.AddField(
            model_name="ledgerentry",
            name="is_immutable",
            field=models.BooleanField(default=True),
        ),
        migrations.AddConstraint(
            model_name="account",
            constraint=models.UniqueConstraint(fields=("chama", "code"), name="uniq_finance_account_code_per_chama"),
        ),
        migrations.AddConstraint(
            model_name="account",
            constraint=models.UniqueConstraint(fields=("chama", "name"), name="uniq_finance_account_name_per_chama"),
        ),
        migrations.AddConstraint(
            model_name="journalentry",
            constraint=models.UniqueConstraint(fields=("chama", "idempotency_key"), name="uniq_journal_idempotency_per_chama"),
        ),
        migrations.AddConstraint(
            model_name="journalentry",
            constraint=models.CheckConstraint(condition=~models.Q(("id", models.F("reversal_of"))), name="journal_reversal_not_self"),
        ),
        migrations.AddConstraint(
            model_name="financialsnapshot",
            constraint=models.UniqueConstraint(fields=("chama", "snapshot_date"), name="uniq_financial_snapshot_per_chama_day"),
        ),
        migrations.AddConstraint(
            model_name="expense",
            constraint=models.CheckConstraint(condition=models.Q(("amount__gt", Decimal("0.00"))), name="expense_amount_positive"),
        ),
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.CheckConstraint(
                condition=models.Q(("journal_entry__isnull", True))
                | (
                    models.Q(("debit__gt", Decimal("0.00")), ("credit", Decimal("0.00")))
                    | models.Q(("credit__gt", Decimal("0.00")), ("debit", Decimal("0.00")))
                ),
                name="ledger_exactly_one_side_populated",
            ),
        ),
        migrations.AddIndex(model_name="account", index=models.Index(fields=["chama", "type", "is_active"], name="finance_acc_chama_t_0f4bdc_idx")),
        migrations.AddIndex(model_name="account", index=models.Index(fields=["chama", "system_managed"], name="finance_acc_chama_s_78f196_idx")),
        migrations.AddIndex(model_name="journalentry", index=models.Index(fields=["chama", "source_type", "posted_at"], name="finance_jou_chama_s_9ebcbc_idx")),
        migrations.AddIndex(model_name="journalentry", index=models.Index(fields=["reference", "posted_at"], name="finance_jou_referen_123e47_idx")),
        migrations.AddIndex(model_name="financialsnapshot", index=models.Index(fields=["chama", "snapshot_date"], name="finance_sna_chama_s_9a92f4_idx")),
        migrations.AddIndex(model_name="expense", index=models.Index(fields=["chama", "expense_date"], name="finance_exp_chama_e_5d9a09_idx")),
        migrations.AddIndex(model_name="expense", index=models.Index(fields=["chama", "status"], name="finance_exp_chama_s_9c1ed7_idx")),
        migrations.AddIndex(model_name="expense", index=models.Index(fields=["category", "expense_date"], name="finance_exp_catego_d9f389_idx")),
        migrations.AddIndex(model_name="ledgerentry", index=models.Index(fields=["journal_entry", "account"], name="finance_led_journal_90ec9c_idx")),
        migrations.AddIndex(model_name="ledgerentry", index=models.Index(fields=["account", "created_at"], name="finance_led_account_d4d7d9_idx")),
    ]
