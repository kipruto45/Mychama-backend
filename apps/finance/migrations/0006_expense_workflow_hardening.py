import uuid
from django.conf import settings
from django.db import migrations, models
from django.db.models import F
import django.db.models.deletion


def sync_existing_expenses(apps, schema_editor):
    Expense = apps.get_model("finance", "Expense")

    Expense.objects.filter(status="reversed").update(status="cancelled")
    Expense.objects.filter(requested_by__isnull=True, created_by__isnull=False).update(
        requested_by=F("created_by")
    )
    Expense.objects.filter(status="paid", paid_at__isnull=True).update(paid_at=F("updated_at"))
    Expense.objects.filter(status="approved", approved_at__isnull=True).update(approved_at=F("updated_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0005_loan_application_workflow"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ExpenseCategory",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="finance_expensecategory_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="finance_expensecategory_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("name", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "chama",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="expense_categories",
                        to="chama.chama",
                    ),
                ),
            ],
            options={
                "ordering": ["name", "-created_at"],
            },
        ),
        migrations.AddField(
            model_name="expense",
            name="approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="expense",
            name="category_ref",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="expenses",
                to="finance.expensecategory",
            ),
        ),
        migrations.AddField(
            model_name="expense",
            name="notes",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="expense",
            name="paid_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="expense",
            name="payment_reference",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="expense",
            name="receipt_file",
            field=models.FileField(blank=True, null=True, upload_to="finance/expense-receipts/"),
        ),
        migrations.AddField(
            model_name="expense",
            name="rejected_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="expense",
            name="rejected_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="rejected_expenses",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="expense",
            name="rejection_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="expense",
            name="requested_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="requested_expenses",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="expense",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("paid", "Paid"),
                    ("cancelled", "Cancelled"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddIndex(
            model_name="expensecategory",
            index=models.Index(fields=["chama", "is_active"], name="finance_expe_chama_i_645ec6_idx"),
        ),
        migrations.AddIndex(
            model_name="expensecategory",
            index=models.Index(fields=["chama", "name"], name="finance_expe_chama_i_2ef651_idx"),
        ),
        migrations.AddConstraint(
            model_name="expensecategory",
            constraint=models.UniqueConstraint(
                fields=("chama", "name"),
                name="uniq_expense_category_name_per_chama",
            ),
        ),
        migrations.RunPython(sync_existing_expenses, migrations.RunPython.noop),
    ]
