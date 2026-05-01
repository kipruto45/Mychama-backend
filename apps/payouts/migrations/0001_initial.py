# Generated migration for payouts app

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('chama', '0001_initial'),  # Depends on chama app
        ('governance', '0001_initial'),  # Depends on governance app
        ('payments', '0001_initial'),  # Depends on payments app
        ('finance', '0001_initial'),  # Depends on finance app
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Payout',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('amount', models.DecimalField(decimal_places=2, help_text='Payout amount in KES', max_digits=14)),
                ('currency', models.CharField(choices=[('KES', 'Kenyan Shilling'), ('USD', 'US Dollar'), ('EUR', 'Euro')], default='KES', max_length=3)),
                ('rotation_position', models.IntegerField(help_text='Position in rotation cycle')),
                ('rotation_cycle', models.IntegerField(help_text='Cycle number for this payout')),
                ('status', models.CharField(choices=[('triggered', 'Triggered'), ('rotation_check', 'Rotation Check'), ('eligibility_check', 'Eligibility Check'), ('ineligible', 'Ineligible (Skip/Defer)'), ('awaiting_treasurer_review', 'Awaiting Treasurer Review'), ('treasury_rejected', 'Rejected by Treasurer'), ('awaiting_chair_approval', 'Awaiting Chairperson Approval'), ('chair_rejected', 'Rejected by Chairperson'), ('approved', 'Approved for Payment'), ('processing', 'Processing Payment'), ('success', 'Payout Completed'), ('failed', 'Payout Failed'), ('hold', 'On Hold (Issue Flagged)'), ('cancelled', 'Cancelled')], db_index=True, default='triggered', max_length=30)),
                ('trigger_type', models.CharField(choices=[('manual', 'Manual Trigger (by Treasurer/Chairperson)'), ('auto', 'Auto Trigger (cycle complete)'), ('scheduled', 'Scheduled Reminder')], default='manual', max_length=20)),
                ('eligibility_status', models.CharField(blank=True, choices=[('eligible', 'Eligible'), ('pending_penalties', 'Outstanding Penalties'), ('active_disputes', 'Active Disputes'), ('overdue_loans', 'Overdue Loans'), ('inactive_member', 'Inactive Member'), ('insufficient_funds', 'Insufficient Funds'), ('multiple_issues', 'Multiple Issues')], max_length=30, null=True)),
                ('eligibility_issues', models.JSONField(default=list, help_text='List of eligibility issues found')),
                ('eligibility_checked_at', models.DateTimeField(blank=True, null=True)),
                ('treasurer_reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('treasurer_rejection_reason', models.TextField(blank=True)),
                ('chairperson_approved_at', models.DateTimeField(blank=True, null=True)),
                ('chairperson_rejection_reason', models.TextField(blank=True)),
                ('payout_method', models.CharField(choices=[('bank_transfer', 'Bank Transfer'), ('mpesa', 'M-Pesa (B2C)'), ('wallet', 'Chama Wallet')], default='mpesa', max_length=20)),
                ('is_on_hold', models.BooleanField(default=False)),
                ('hold_reason', models.TextField(blank=True)),
                ('hold_flagged_at', models.DateTimeField(blank=True, null=True)),
                ('hold_resolved_at', models.DateTimeField(blank=True, null=True)),
                ('payment_started_at', models.DateTimeField(blank=True, null=True)),
                ('payment_completed_at', models.DateTimeField(blank=True, null=True)),
                ('payment_failed_at', models.DateTimeField(blank=True, null=True)),
                ('failure_reason', models.TextField(blank=True)),
                ('failure_code', models.CharField(blank=True, max_length=50)),
                ('retry_count', models.IntegerField(default=0)),
                ('max_retries', models.IntegerField(default=3)),
                ('receipt_generated_at', models.DateTimeField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('skip_reason', models.CharField(blank=True, help_text='Reason for skipping to next member', max_length=255)),
                ('defer_reason', models.CharField(blank=True, help_text='Reason for deferring to next cycle', max_length=255)),
                ('approval_request', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payouts', to='governance.approvalrequest')),
                ('chairperson_approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payouts_approved', to=settings.AUTH_USER_MODEL)),
                ('chama', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payouts', to='chama.chama')),
                ('hold_flagged_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payouts_on_hold', to=settings.AUTH_USER_MODEL)),
                ('hold_resolved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payouts_hold_resolved', to=settings.AUTH_USER_MODEL)),
                ('member', models.ForeignKey(help_text='Member receiving payout', on_delete=django.db.models.deletion.PROTECT, related_name='payouts_received', to='chama.membership')),
                ('payment_intent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payouts', to='payments.paymentintent')),
                ('ledger_entry', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payouts', to='finance.ledgerentry')),
                ('treasurer_reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payouts_reviewed', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='PayoutRotation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('current_position', models.IntegerField(default=0, help_text='Index in rotation queue')),
                ('rotation_cycle', models.IntegerField(default=1, help_text='Current cycle number (resets after full rotation)')),
                ('members_in_rotation', models.JSONField(default=list, help_text='Ordered list of member IDs in rotation')),
                ('last_updated_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('chama', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='payout_rotation', to='chama.chama')),
                ('last_completed_payout', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='rotation_completion', to='payouts.payout')),
            ],
            options={
                'ordering': ['-chama__created_at'],
            },
        ),
        migrations.CreateModel(
            name='PayoutEligibilityCheck',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('result', models.CharField(choices=[('eligible', 'Eligible'), ('pending_penalties', 'Outstanding Penalties'), ('active_disputes', 'Active Disputes'), ('overdue_loans', 'Overdue Loans'), ('inactive_member', 'Inactive Member'), ('insufficient_funds', 'Insufficient Funds'), ('multiple_issues', 'Multiple Issues')], max_length=30)),
                ('has_outstanding_penalties', models.BooleanField(default=False)),
                ('penalty_amount', models.DecimalField(decimal_places=2, default='0.00', max_digits=14)),
                ('active_penalties', models.JSONField(default=list, help_text='IDs of active penalties')),
                ('has_active_disputes', models.BooleanField(default=False)),
                ('active_disputes', models.JSONField(default=list, help_text='IDs of active disputes')),
                ('has_overdue_loans', models.BooleanField(default=False)),
                ('overdue_loan_amount', models.DecimalField(decimal_places=2, default='0.00', max_digits=14)),
                ('overdue_loans', models.JSONField(default=list, help_text='IDs of overdue loans')),
                ('member_is_active', models.BooleanField(default=True)),
                ('wallet_has_funds', models.BooleanField(default=True)),
                ('available_balance', models.DecimalField(decimal_places=2, default='0.00', max_digits=14)),
                ('checked_at', models.DateTimeField(auto_now_add=True)),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='eligibility_checks', to='chama.membership')),
                ('payout', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='eligibility_check', to='payouts.payout')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='PayoutAuditLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('action', models.CharField(help_text='Action performed (e.g., TRIGGERED, APPROVED, REJECTED, PAID)', max_length=50)),
                ('previous_status', models.CharField(blank=True, max_length=30)),
                ('new_status', models.CharField(blank=True, max_length=30)),
                ('details', models.JSONField(blank=True, default=dict)),
                ('reason', models.TextField(blank=True)),
                ('is_immutable', models.BooleanField(default=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payout_audit_actions', to=settings.AUTH_USER_MODEL)),
                ('payout', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='audit_logs', to='payouts.payout')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='payoutrotation',
            index=models.Index(fields=['chama'], name='payouts_payo_chama_idx'),
        ),
        migrations.AddIndex(
            model_name='payout',
            index=models.Index(fields=['chama', 'status', 'created_at'], name='payouts_payo_chama_status_idx'),
        ),
        migrations.AddIndex(
            model_name='payout',
            index=models.Index(fields=['member', 'status'], name='payouts_payo_member_status_idx'),
        ),
        migrations.AddIndex(
            model_name='payout',
            index=models.Index(fields=['status', 'created_at'], name='payouts_payo_status_created_idx'),
        ),
        migrations.AddIndex(
            model_name='payout',
            index=models.Index(fields=['rotation_cycle', 'rotation_position'], name='payouts_payo_rotation_idx'),
        ),
        migrations.AddConstraint(
            model_name='payout',
            constraint=models.CheckConstraint(condition=models.Q(('amount__gt', 0)), name='payout_amount_positive'),
        ),
        migrations.AddIndex(
            model_name='payouteligibilitycheck',
            index=models.Index(fields=['member', 'result'], name='payouts_payo_member_result_idx'),
        ),
        migrations.AddIndex(
            model_name='payoutauditlog',
            index=models.Index(fields=['payout', 'action'], name='payouts_payo_payout_action_idx'),
        ),
        migrations.AddIndex(
            model_name='payoutauditlog',
            index=models.Index(fields=['created_at'], name='payouts_payo_created_idx'),
        ),
    ]
