import uuid
from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0004_unified_reconciliation_cases"),
    ]

    operations = [
        migrations.CreateModel(
            name="ManualPaymentApprovalPolicy",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("cash_maker_checker_enabled", models.BooleanField(default=True)),
                ("bank_maker_checker_enabled", models.BooleanField(default=True)),
                ("block_payer_self_approval", models.BooleanField(default=True)),
                ("require_cash_receipt_number", models.BooleanField(default=False)),
                ("require_cash_proof", models.BooleanField(default=False)),
                ("require_bank_proof_document", models.BooleanField(default=True)),
                ("require_bank_transfer_reference", models.BooleanField(default=True)),
                ("dual_approval_threshold", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("allowed_cash_recorder_roles", models.JSONField(blank=True, default=list)),
                ("allowed_cash_verifier_roles", models.JSONField(blank=True, default=list)),
                ("allowed_bank_verifier_roles", models.JSONField(blank=True, default=list)),
                ("allowed_reconciliation_roles", models.JSONField(blank=True, default=list)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("chama", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="manual_payment_policy", to="chama.chama")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_manualpaymentapprovalpolicy_created", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_manualpaymentapprovalpolicy_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Manual Payment Approval Policy",
                "verbose_name_plural": "Manual Payment Approval Policies",
            },
        ),
        migrations.CreateModel(
            name="PaymentStatementImport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("payment_method", models.CharField(choices=[("mpesa", "M-Pesa"), ("card", "Card"), ("cash", "Cash"), ("bank", "Bank Transfer")], max_length=20)),
                ("provider_name", models.CharField(blank=True, max_length=50)),
                ("source_name", models.CharField(blank=True, max_length=150)),
                ("statement_date", models.DateField(blank=True, null=True)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("processed", "Processed"), ("failed", "Failed")], default="pending", max_length=20)),
                ("total_rows", models.PositiveIntegerField(default=0)),
                ("matched_rows", models.PositiveIntegerField(default=0)),
                ("mismatch_rows", models.PositiveIntegerField(default=0)),
                ("unmatched_rows", models.PositiveIntegerField(default=0)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("chama", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payment_statement_imports", to="chama.chama")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_paymentstatementimport_created", to=settings.AUTH_USER_MODEL)),
                ("imported_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="imported_payment_statements", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_paymentstatementimport_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="PaymentStatementLine",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("line_number", models.PositiveIntegerField()),
                ("external_reference", models.CharField(blank=True, max_length=255)),
                ("payer_reference", models.CharField(blank=True, max_length=120)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("currency", models.CharField(choices=[("KES", "Kenyan Shilling"), ("USD", "US Dollar"), ("EUR", "Euro"), ("GBP", "Pound Sterling")], default="KES", max_length=3)),
                ("transaction_date", models.DateTimeField(blank=True, null=True)),
                ("match_status", models.CharField(choices=[("matched", "Matched"), ("pending_review", "Pending Review"), ("unmatched", "Unmatched"), ("mismatch", "Mismatch"), ("duplicate", "Duplicate")], default="pending_review", max_length=20)),
                ("raw_payload", models.JSONField(blank=True, default=dict)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_paymentstatementline_created", to=settings.AUTH_USER_MODEL)),
                ("matched_payment_intent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="statement_lines", to="payments.paymentintent")),
                ("matched_transaction", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="statement_lines", to="payments.paymenttransaction")),
                ("reconciliation_case", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="statement_lines", to="payments.paymentreconciliationcase")),
                ("statement_import", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lines", to="payments.paymentstatementimport")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_paymentstatementline_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["line_number", "created_at"],
            },
        ),
        migrations.CreateModel(
            name="CashPaymentDetails",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("receipt_number", models.CharField(blank=True, max_length=100)),
                ("proof_photo", models.ImageField(blank=True, upload_to="cash_proofs/")),
                ("notes", models.TextField(blank=True)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_cashpaymentdetails_created", to=settings.AUTH_USER_MODEL)),
                ("payment_intent", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="cash_details", to="payments.paymentintent")),
                ("received_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="received_cash_payments", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_cashpaymentdetails_updated", to=settings.AUTH_USER_MODEL)),
                ("verified_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="verified_cash_payments", to=settings.AUTH_USER_MODEL)),
            ],
            options={},
        ),
        migrations.CreateModel(
            name="BankPaymentDetails",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("bank_name", models.CharField(max_length=100)),
                ("account_number", models.CharField(max_length=50)),
                ("account_name", models.CharField(blank=True, max_length=100)),
                ("transfer_reference", models.CharField(blank=True, max_length=100)),
                ("proof_document", models.FileField(blank=True, upload_to="bank_proofs/")),
                ("notes", models.TextField(blank=True)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_bankpaymentdetails_created", to=settings.AUTH_USER_MODEL)),
                ("payment_intent", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="bank_details", to="payments.paymentintent")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_bankpaymentdetails_updated", to=settings.AUTH_USER_MODEL)),
                ("verified_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="verified_bank_payments", to=settings.AUTH_USER_MODEL)),
            ],
            options={},
        ),
        migrations.AddField(
            model_name="cashpaymentdetails",
            name="first_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="cashpaymentdetails",
            name="first_verified_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="first_verified_cash_payments", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="bankpaymentdetails",
            name="first_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="bankpaymentdetails",
            name="first_verified_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="first_verified_bank_payments", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddIndex(
            model_name="cashpaymentdetails",
            index=models.Index(fields=["received_by"], name="payments_cash_received_idx"),
        ),
        migrations.AddIndex(
            model_name="cashpaymentdetails",
            index=models.Index(fields=["receipt_number"], name="payments_cash_receipt_idx"),
        ),
        migrations.AddIndex(
            model_name="bankpaymentdetails",
            index=models.Index(fields=["bank_name"], name="payments_bank_name_idx"),
        ),
        migrations.AddIndex(
            model_name="bankpaymentdetails",
            index=models.Index(fields=["transfer_reference"], name="payments_bank_transfer_idx"),
        ),
        migrations.AddIndex(
            model_name="paymentstatementimport",
            index=models.Index(fields=["chama", "payment_method", "created_at"], name="payments_stmtimport_chama_method_created_idx"),
        ),
        migrations.AddIndex(
            model_name="paymentstatementimport",
            index=models.Index(fields=["status", "created_at"], name="payments_stmtimport_status_created_idx"),
        ),
        migrations.AddIndex(
            model_name="paymentstatementline",
            index=models.Index(fields=["statement_import", "match_status"], name="payments_stmtline_import_status_idx"),
        ),
        migrations.AddIndex(
            model_name="paymentstatementline",
            index=models.Index(fields=["external_reference"], name="payments_stmtline_external_ref_idx"),
        ),
        migrations.AddIndex(
            model_name="paymentstatementline",
            index=models.Index(fields=["amount", "currency"], name="payments_stmtline_amount_currency_idx"),
        ),
    ]
