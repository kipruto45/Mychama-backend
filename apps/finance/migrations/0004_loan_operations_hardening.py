from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import uuid


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("finance", "0003_double_entry_finance"),
    ]

    operations = [
        migrations.AddField(
            model_name="installmentschedule",
            name="paid_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="installmentschedule",
            name="paid_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="installmentschedule",
            name="paid_interest",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="installmentschedule",
            name="paid_penalty",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="installmentschedule",
            name="paid_principal",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="loan",
            name="defaulted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="loan",
            name="due_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="loan",
            name="outstanding_interest",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="loan",
            name="outstanding_penalty",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="loan",
            name="outstanding_principal",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="loan",
            name="purpose",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="loan",
            name="rejected_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="loan",
            name="repaid_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="loan",
            name="total_due",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="loanguarantor",
            name="review_note",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="repayment",
            name="allocation_breakdown",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name="LoanRecoveryAction",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "action_type",
                    models.CharField(
                        choices=[
                            ("reminder", "Reminder"),
                            ("penalty_applied", "Penalty Applied"),
                            ("guarantor_notified", "Guarantor Notified"),
                            ("guarantor_recovery", "Guarantor Recovery"),
                            ("offset_from_savings", "Offset From Savings"),
                            ("restructure_requested", "Restructure Requested"),
                            ("restructure_approved", "Restructure Approved"),
                            ("penalty_waived", "Penalty Waived"),
                            ("write_off", "Write Off"),
                            ("manual_note", "Manual Note"),
                        ],
                        max_length=40,
                    ),
                ),
                ("amount", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("notes", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="finance_loanrecoveryaction_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="finance_loanrecoveryaction_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "loan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recovery_actions",
                        to="finance.loan",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="LoanRestructure",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("old_terms_snapshot", models.JSONField(blank=True, default=dict)),
                ("new_terms_snapshot", models.JSONField(blank=True, default=dict)),
                ("approved_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approved_loan_restructures",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="finance_loanrestructure_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "loan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="restructures",
                        to="finance.loan",
                    ),
                ),
                (
                    "source_request",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="applied_restructures",
                        to="finance.loanrestructurerequest",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="finance_loanrestructure_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-approved_at", "-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="loan",
            index=models.Index(fields=["chama", "due_date"], name="finance_loa_chama_i_b7bf1e_idx"),
        ),
        migrations.AddIndex(
            model_name="loan",
            index=models.Index(fields=["status", "due_date"], name="finance_loa_status__5831f4_idx"),
        ),
        migrations.AddIndex(
            model_name="loanrecoveryaction",
            index=models.Index(fields=["loan", "action_type", "created_at"], name="finance_recovery_loan_act_idx"),
        ),
        migrations.AddIndex(
            model_name="loanrecoveryaction",
            index=models.Index(fields=["action_type", "created_at"], name="finance_recovery_action_idx"),
        ),
        migrations.AddIndex(
            model_name="loanrestructure",
            index=models.Index(fields=["loan", "approved_at"], name="finance_restructure_loan_idx"),
        ),
        migrations.AddConstraint(
            model_name="loan",
            constraint=models.CheckConstraint(
                condition=models.Q(outstanding_principal__gte=0),
                name="loan_outstanding_principal_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="loan",
            constraint=models.CheckConstraint(
                condition=models.Q(outstanding_interest__gte=0),
                name="loan_outstanding_interest_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="loan",
            constraint=models.CheckConstraint(
                condition=models.Q(outstanding_penalty__gte=0),
                name="loan_outstanding_penalty_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="loan",
            constraint=models.CheckConstraint(
                condition=models.Q(total_due__gte=0),
                name="loan_total_due_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="installmentschedule",
            constraint=models.CheckConstraint(
                condition=models.Q(paid_amount__gte=0),
                name="installment_paid_amount_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="installmentschedule",
            constraint=models.CheckConstraint(
                condition=models.Q(paid_principal__gte=0),
                name="installment_paid_principal_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="installmentschedule",
            constraint=models.CheckConstraint(
                condition=models.Q(paid_interest__gte=0),
                name="installment_paid_interest_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="installmentschedule",
            constraint=models.CheckConstraint(
                condition=models.Q(paid_penalty__gte=0),
                name="installment_paid_penalty_non_negative",
            ),
        ),
    ]
