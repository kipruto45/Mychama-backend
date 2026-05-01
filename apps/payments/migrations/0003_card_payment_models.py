import django.db.models.deletion
import django.utils.timezone
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0001_initial"),
        ("payments", "0002_payment_intent_hardening"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CardPaymentIntent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                (
                    "currency",
                    models.CharField(
                        choices=[
                            ("KES", "Kenyan Shilling"),
                            ("USD", "US Dollar"),
                            ("EUR", "Euro"),
                            ("GBP", "British Pound"),
                        ],
                        default="KES",
                        max_length=3,
                    ),
                ),
                (
                    "purpose",
                    models.CharField(
                        choices=[
                            ("contribution", "Contribution"),
                            ("loan_repayment", "Loan Repayment"),
                            ("fee", "Fee"),
                            ("penalty", "Penalty"),
                            ("other", "Other"),
                        ],
                        default="contribution",
                        max_length=30,
                    ),
                ),
                ("description", models.TextField(blank=True)),
                (
                    "provider",
                    models.CharField(
                        choices=[
                            ("stripe", "Stripe"),
                            ("flutterwave", "Flutterwave"),
                            ("paystack", "Paystack"),
                        ],
                        default="stripe",
                        max_length=20,
                    ),
                ),
                ("provider_intent_id", models.CharField(db_index=True, max_length=255, unique=True)),
                ("client_secret", models.CharField(blank=True, max_length=255)),
                ("checkout_url", models.URLField(blank=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("initiated", "Initiated"),
                            ("pending_authentication", "Pending Authentication"),
                            ("pending", "Pending"),
                            ("success", "Success"),
                            ("failed", "Failed"),
                            ("cancelled", "Cancelled"),
                            ("expired", "Expired"),
                            ("refunded", "Refunded"),
                            ("partially_refunded", "Partially Refunded"),
                        ],
                        default="initiated",
                        max_length=30,
                    ),
                ),
                ("idempotency_key", models.CharField(max_length=100, unique=True)),
                ("reference", models.CharField(blank=True, max_length=100)),
                ("failure_reason", models.TextField(blank=True)),
                ("failure_code", models.CharField(blank=True, max_length=50)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                (
                    "chama",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="card_payment_intents",
                        to="chama.chama",
                    ),
                ),
                (
                    "contribution",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="card_payment_intents",
                        to="finance.contribution",
                    ),
                ),
                (
                    "contribution_type",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="card_payment_intents",
                        to="finance.contributiontype",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="card_payment_intents",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="CardPaymentWebhook",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "provider",
                    models.CharField(
                        choices=[
                            ("stripe", "Stripe"),
                            ("flutterwave", "Flutterwave"),
                            ("paystack", "Paystack"),
                        ],
                        max_length=20,
                    ),
                ),
                ("event_type", models.CharField(db_index=True, max_length=100)),
                ("provider_reference", models.CharField(blank=True, db_index=True, max_length=255)),
                ("signature_valid", models.BooleanField(blank=True, null=True)),
                ("signature", models.CharField(blank=True, max_length=255)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("headers", models.JSONField(blank=True, default=dict)),
                ("processed", models.BooleanField(default=False)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("processing_error", models.TextField(blank=True)),
                ("source_ip", models.GenericIPAddressField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="CardPaymentTransaction",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("provider_reference", models.CharField(db_index=True, max_length=255, unique=True)),
                (
                    "provider_name",
                    models.CharField(
                        choices=[
                            ("stripe", "Stripe"),
                            ("flutterwave", "Flutterwave"),
                            ("paystack", "Paystack"),
                        ],
                        max_length=20,
                    ),
                ),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                (
                    "currency",
                    models.CharField(
                        choices=[
                            ("KES", "Kenyan Shilling"),
                            ("USD", "US Dollar"),
                            ("EUR", "Euro"),
                            ("GBP", "British Pound"),
                        ],
                        max_length=3,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("initiated", "Initiated"),
                            ("pending_authentication", "Pending Authentication"),
                            ("pending", "Pending"),
                            ("success", "Success"),
                            ("failed", "Failed"),
                            ("cancelled", "Cancelled"),
                            ("expired", "Expired"),
                            ("refunded", "Refunded"),
                            ("partially_refunded", "Partially Refunded"),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "card_brand",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("visa", "Visa"),
                            ("mastercard", "Mastercard"),
                            ("amex", "American Express"),
                            ("discover", "Discover"),
                            ("diners", "Diners Club"),
                            ("jcb", "JCB"),
                            ("unionpay", "UnionPay"),
                            ("unknown", "Unknown"),
                        ],
                        max_length=20,
                    ),
                ),
                ("card_last4", models.CharField(blank=True, max_length=4)),
                ("card_country", models.CharField(blank=True, max_length=2)),
                ("authorization_code", models.CharField(blank=True, max_length=100)),
                ("auth_code", models.CharField(blank=True, max_length=100)),
                ("raw_response", models.JSONField(blank=True, default=dict)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("failed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "payment_intent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="transactions",
                        to="payments.cardpaymentintent",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="CardPaymentReceipt",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("reference_number", models.CharField(db_index=True, max_length=100, unique=True)),
                ("receipt_number", models.CharField(db_index=True, max_length=100, unique=True)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                (
                    "currency",
                    models.CharField(
                        choices=[
                            ("KES", "Kenyan Shilling"),
                            ("USD", "US Dollar"),
                            ("EUR", "Euro"),
                            ("GBP", "British Pound"),
                        ],
                        max_length=3,
                    ),
                ),
                ("card_brand", models.CharField(blank=True, max_length=20)),
                ("card_last4", models.CharField(blank=True, max_length=4)),
                ("issued_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "issued_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="issued_card_receipts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "payment_intent",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="receipt",
                        to="payments.cardpaymentintent",
                    ),
                ),
                (
                    "transaction",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="receipt",
                        to="payments.cardpaymenttransaction",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-issued_at"],
            },
        ),
        migrations.CreateModel(
            name="CardPaymentAuditLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("event", models.CharField(max_length=100)),
                ("previous_status", models.CharField(blank=True, max_length=30)),
                ("new_status", models.CharField(blank=True, max_length=30)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.TextField(blank=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="card_payment_audit_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "payment_intent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="audit_logs",
                        to="payments.cardpaymentintent",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="cardpaymentintent",
            constraint=models.UniqueConstraint(fields=("chama", "idempotency_key"), name="uniq_card_payment_intent_idempotency_per_chama"),
        ),
        migrations.AddConstraint(
            model_name="cardpaymentintent",
            constraint=models.CheckConstraint(condition=models.Q(amount__gt=Decimal("0.00")), name="card_payment_intent_amount_positive"),
        ),
        migrations.AddConstraint(
            model_name="cardpaymenttransaction",
            constraint=models.CheckConstraint(condition=models.Q(amount__gt=Decimal("0.00")), name="card_payment_transaction_amount_positive"),
        ),
        migrations.AddIndex(model_name="cardpaymentintent", index=models.Index(fields=["chama", "status", "created_at"], name="payments_cpi_chama_status_created_idx")),
        migrations.AddIndex(model_name="cardpaymentintent", index=models.Index(fields=["user", "status", "created_at"], name="payments_cpi_user_status_created_idx")),
        migrations.AddIndex(model_name="cardpaymentintent", index=models.Index(fields=["provider", "status"], name="payments_cpi_provider_status_idx")),
        migrations.AddIndex(model_name="cardpaymentintent", index=models.Index(fields=["provider_intent_id"], name="payments_cpi_provider_intent_idx")),
        migrations.AddIndex(model_name="cardpaymentintent", index=models.Index(fields=["contribution"], name="payments_cpi_contribution_idx")),
        migrations.AddIndex(model_name="cardpaymentintent", index=models.Index(fields=["contribution_type"], name="payments_cpi_contribution_type_idx")),
        migrations.AddIndex(model_name="cardpaymenttransaction", index=models.Index(fields=["payment_intent", "status", "created_at"], name="payments_cpt_intent_status_created_idx")),
        migrations.AddIndex(model_name="cardpaymenttransaction", index=models.Index(fields=["provider_name", "status"], name="payments_cpt_provider_status_idx")),
        migrations.AddIndex(model_name="cardpaymenttransaction", index=models.Index(fields=["provider_reference"], name="payments_cpt_provider_reference_idx")),
        migrations.AddIndex(model_name="cardpaymenttransaction", index=models.Index(fields=["card_brand", "card_last4"], name="payments_cpt_card_mask_idx")),
        migrations.AddIndex(model_name="cardpaymentwebhook", index=models.Index(fields=["provider", "event_type", "created_at"], name="payments_cpw_provider_event_created_idx")),
        migrations.AddIndex(model_name="cardpaymentwebhook", index=models.Index(fields=["provider_reference"], name="payments_cpw_provider_reference_idx")),
        migrations.AddIndex(model_name="cardpaymentwebhook", index=models.Index(fields=["processed", "created_at"], name="payments_cpw_processed_created_idx")),
        migrations.AddIndex(model_name="cardpaymentreceipt", index=models.Index(fields=["reference_number"], name="payments_cpr_reference_idx")),
        migrations.AddIndex(model_name="cardpaymentreceipt", index=models.Index(fields=["receipt_number"], name="payments_cpr_receipt_idx")),
        migrations.AddIndex(model_name="cardpaymentreceipt", index=models.Index(fields=["issued_at"], name="payments_cpr_issued_idx")),
        migrations.AddIndex(model_name="cardpaymentauditlog", index=models.Index(fields=["payment_intent", "created_at"], name="payments_cpal_intent_created_idx")),
        migrations.AddIndex(model_name="cardpaymentauditlog", index=models.Index(fields=["event", "created_at"], name="payments_cpal_event_created_idx")),
        migrations.AddIndex(model_name="cardpaymentauditlog", index=models.Index(fields=["actor", "created_at"], name="payments_cpal_actor_created_idx")),
    ]
