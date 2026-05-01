from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("finance", "0004_loan_operations_hardening"),
    ]

    operations = [
        migrations.CreateModel(
            name="LoanApplication",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("requested_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("requested_term_months", models.PositiveIntegerField()),
                ("purpose", models.TextField(blank=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("submitted", "Submitted"),
                            ("in_review", "In Review"),
                            ("treasurer_approved", "Treasurer Approved"),
                            ("committee_approved", "Committee Approved"),
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                            ("disbursed", "Disbursed"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="submitted",
                        max_length=30,
                    ),
                ),
                (
                    "eligibility_status",
                    models.CharField(
                        choices=[("eligible", "Eligible"), ("ineligible", "Ineligible")],
                        default="ineligible",
                        max_length=20,
                    ),
                ),
                ("recommended_max_amount", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("eligibility_snapshot", models.JSONField(blank=True, default=dict)),
                ("rejection_reason", models.TextField(blank=True)),
                ("submitted_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("disbursed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approved_loan_applications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "chama",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="loan_applications",
                        to="chama.chama",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="finance_loanapplication_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "loan_product",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="loan_applications",
                        to="finance.loanproduct",
                    ),
                ),
                (
                    "member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="loan_applications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reviewed_loan_applications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="finance_loanapplication_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-submitted_at", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="LoanApplicationApproval",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "stage",
                    models.CharField(
                        choices=[
                            ("treasurer_review", "Treasurer Review"),
                            ("committee_approval", "Committee Approval"),
                            ("admin_approval", "Admin Approval"),
                            ("disbursement", "Disbursement"),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "decision",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("note", models.TextField(blank=True)),
                ("acted_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="loan_application_approval_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="finance_loanapplicationapproval_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "loan_application",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="approval_logs",
                        to="finance.loanapplication",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="finance_loanapplicationapproval_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["acted_at", "created_at"],
            },
        ),
        migrations.CreateModel(
            name="LoanApplicationGuarantor",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("guaranteed_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("proposed", "Proposed"),
                            ("accepted", "Accepted"),
                            ("rejected", "Rejected"),
                            ("released", "Released"),
                        ],
                        default="proposed",
                        max_length=20,
                    ),
                ),
                ("review_note", models.TextField(blank=True)),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("rejected_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="finance_loanapplicationguarantor_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "guarantor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="loan_application_guarantees",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "loan_application",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="guarantors",
                        to="finance.loanapplication",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="finance_loanapplicationguarantor_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "id"],
            },
        ),
        migrations.AddField(
            model_name="loanapplication",
            name="created_loan",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="source_application_record",
                to="finance.loan",
            ),
        ),
        migrations.AddIndex(
            model_name="loanapplication",
            index=models.Index(fields=["chama", "status", "submitted_at"], name="finance_loa_chama_s_ef57c9_idx"),
        ),
        migrations.AddIndex(
            model_name="loanapplication",
            index=models.Index(fields=["member", "status", "submitted_at"], name="finance_loa_member__1501f0_idx"),
        ),
        migrations.AddIndex(
            model_name="loanapplication",
            index=models.Index(fields=["status", "submitted_at"], name="finance_loa_status__313174_idx"),
        ),
        migrations.AddIndex(
            model_name="loanapplication",
            index=models.Index(fields=["loan_product", "status"], name="finance_loa_loan_pr_366ec3_idx"),
        ),
        migrations.AddIndex(
            model_name="loanapplicationapproval",
            index=models.Index(fields=["loan_application", "stage", "acted_at"], name="finance_loa_loan_ap_3956ad_idx"),
        ),
        migrations.AddIndex(
            model_name="loanapplicationapproval",
            index=models.Index(fields=["decision", "acted_at"], name="finance_loa_decisio_010ffc_idx"),
        ),
        migrations.AddIndex(
            model_name="loanapplicationguarantor",
            index=models.Index(fields=["loan_application", "status"], name="finance_loa_loan_ap_b9eae0_idx"),
        ),
        migrations.AddIndex(
            model_name="loanapplicationguarantor",
            index=models.Index(fields=["guarantor", "status"], name="finance_loa_guarant_705193_idx"),
        ),
        migrations.AddConstraint(
            model_name="loanapplication",
            constraint=models.CheckConstraint(
                condition=models.Q(requested_amount__gt=0),
                name="loan_application_amount_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="loanapplication",
            constraint=models.CheckConstraint(
                condition=models.Q(requested_term_months__gt=0),
                name="loan_application_term_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="loanapplication",
            constraint=models.CheckConstraint(
                condition=models.Q(recommended_max_amount__gte=0),
                name="loan_application_recommended_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="loanapplicationguarantor",
            constraint=models.UniqueConstraint(
                fields=("loan_application", "guarantor"),
                name="uniq_loan_application_guarantor",
            ),
        ),
        migrations.AddConstraint(
            model_name="loanapplicationguarantor",
            constraint=models.CheckConstraint(
                condition=models.Q(guaranteed_amount__gt=0),
                name="loan_application_guaranteed_amount_positive",
            ),
        ),
    ]
