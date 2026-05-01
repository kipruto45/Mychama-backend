from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('issues', '0001_initial'),
        ('chama', '0001_initial'),
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='IssueEvidence',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('file', models.FileField(upload_to='issue_evidence/%Y/%m/%d')),
                ('evidence_type', models.CharField(choices=[('document', 'Document'), ('image', 'Image'), ('screenshot', 'Screenshot'), ('receipt', 'Receipt'), ('other', 'Other')], default='other', max_length=20)),
                ('caption', models.CharField(blank=True, max_length=500)),
                ('content_type', models.CharField(blank=True, max_length=120)),
                ('size', models.PositiveBigIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='IssueStatusHistory',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('from_status', models.CharField(blank=True, max_length=30)),
                ('to_status', models.CharField(max_length=30)),
                ('reason', models.TextField(blank=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['created_at'],
                'verbose_name_plural': 'Issue status histories',
            },
        ),
        migrations.CreateModel(
            name='IssueAssignmentHistory',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('assigned_role', models.CharField(blank=True, max_length=30)),
                ('note', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='IssueResolution',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('resolution_type', models.CharField(choices=[('ledger_adjustment', 'Ledger Adjustment'), ('refund', 'Refund'), ('penalty_waiver', 'Penalty Waiver'), ('warning', 'Warning'), ('suspension', 'Suspension'), ('dismissal', 'Dismissal'), ('member_notification', 'Member Notification'), ('manual_action', 'Manual Action'), ('other', 'Other')], max_length=30)),
                ('summary', models.TextField()),
                ('detailed_action_taken', models.TextField(blank=True)),
                ('financial_adjustment_amount', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('rejected_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('proposed', 'Proposed'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('executed', 'Executed')], default='proposed', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='IssueReopenRequest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('reason', models.TextField()),
                ('decision', models.CharField(choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='pending', max_length=20)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('decision_note', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='IssueRating',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('score', models.PositiveSmallIntegerField()),
                ('feedback', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='IssueAutoTriggerLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('trigger_type', models.CharField(choices=[('missed_payment', 'Missed Payment'), ('overdue_loan', 'Overdue Loan'), ('quorum_failure', 'Quorum Failure'), ('other', 'Other')], max_length=30)),
                ('linked_object_type', models.CharField(blank=True, max_length=100)),
                ('linked_object_id', models.UUIDField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AlterField(
            model_name='issue',
            name='status',
            field=models.CharField(choices=[('open', 'Open'), ('pending_assignment', 'Pending Assignment'), ('assigned', 'Assigned'), ('clarification_requested', 'Clarification Requested'), ('under_investigation', 'Under Investigation'), ('in_progress', 'In Progress'), ('resolution_proposed', 'Resolution Proposed'), ('awaiting_chairperson_approval', 'Awaiting Chairperson Approval'), ('resolved', 'Resolved'), ('dismissed', 'Dismissed'), ('escalated', 'Escalated'), ('in_vote', 'In Vote'), ('reopened', 'Reopened'), ('closed', 'Closed')], default='open', max_length=30),
        ),
        migrations.AddField(
            model_name='issue',
            name='issue_code',
            field=models.CharField(blank=True, db_index=True, max_length=20, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='issue',
            name='source_type',
            field=models.CharField(choices=[('member', 'Member'), ('chairperson', 'Chairperson'), ('treasurer', 'Treasurer'), ('admin', 'Admin'), ('system', 'System')], default='member', max_length=20),
        ),
        migrations.AddField(
            model_name='issue',
            name='issue_scope',
            field=models.CharField(choices=[('personal', 'Personal'), ('group', 'Group'), ('financial', 'Financial'), ('operational', 'Operational')], default='personal', max_length=20),
        ),
        migrations.AddField(
            model_name='issue',
            name='assigned_role',
            field=models.CharField(blank=True, choices=[('chairperson', 'Chairperson'), ('treasurer', 'Treasurer'), ('committee', 'Committee'), ('admin', 'Admin')], max_length=30),
        ),
        migrations.AddField(
            model_name='issue',
            name='reopened_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='issue',
            name='escalation_type',
            field=models.CharField(blank=True, choices=[('committee', 'Committee'), ('full_group_vote', 'Full Group Vote')], max_length=30),
        ),
        migrations.AddField(
            model_name='issue',
            name='escalation_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='issue',
            name='chairperson_approved',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='issue',
            name='chairperson_approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RemoveIndex(
            model_name='issue',
            name='issues_issu_chama_i_80010f_idx',
        ),
        migrations.RenameField(
            model_name='issue',
            old_name='priority',
            new_name='severity',
        ),
        migrations.AddIndex(
            model_name='issue',
            index=models.Index(fields=['chama', 'status', 'severity'], name='issues_issu_chama_i_80010f_idx'),
        ),
        migrations.AlterField(
            model_name='issue',
            name='category',
            field=models.CharField(choices=[('payment_dispute', 'Payment Dispute'), ('member_conduct', 'Member Conduct'), ('governance', 'Governance'), ('financial', 'Financial'), ('operational', 'Operational'), ('loan_dispute', 'Loan Dispute')], default='operational', max_length=30),
        ),
        migrations.AlterField(
            model_name='issuecomment',
            name='message',
            field=models.TextField(),
        ),
        migrations.AddField(
            model_name='issuecomment',
            name='comment_type',
            field=models.CharField(choices=[('public_update', 'Public Update'), ('internal_note', 'Internal Note'), ('clarification', 'Clarification'), ('resolution_note', 'Resolution Note')], default='public_update', max_length=20),
        ),
        migrations.AddField(
            model_name='issuecomment',
            name='visibility',
            field=models.CharField(choices=[('member_visible', 'Member Visible'), ('internal_only', 'Internal Only')], default='member_visible', max_length=20),
        ),
        migrations.AddField(
            model_name='issuecomment',
            name='is_clarification_response',
            field=models.BooleanField(default=False),
        ),
    ]