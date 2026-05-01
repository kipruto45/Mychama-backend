from celery.schedules import crontab
from django.conf import settings

CELERY_BEAT_SCHEDULE = {
    "notifications-process-scheduled": {
        "task": "apps.automations.tasks.notifications_process_scheduled_job",
        "schedule": crontab(minute="*/5"),
        "options": {"queue": "notifications"},
    },
    "notifications-retry-failed": {
        "task": "apps.automations.tasks.notifications_retry_failed_job",
        "schedule": crontab(minute="*/10"),
        "options": {"queue": "notifications"},
    },
    "notifications-retry-push": {
        "task": "apps.notifications.tasks.retry_failed_push_deliveries",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "notifications"},
    },
    "notifications-dispatch-announcements": {
        "task": "apps.notifications.tasks.dispatch_scheduled_announcements",
        "schedule": crontab(minute="*/5"),
        "options": {"queue": "notifications"},
    },
    "notifications-unread-announcement-followups": {
        "task": "apps.notifications.tasks.remind_unread_high_priority_announcements",
        "schedule": crontab(hour="*/2", minute=5),
        "options": {"queue": "notifications"},
    },
    "finance-contribution-automation": {
        "task": "apps.finance.tasks.contributions_schedule_automation_sweep",
        "schedule": crontab(hour=7, minute=0),
        "options": {"queue": "finance"},
    },
    "finance-loans-due-soon": {
        "task": "apps.finance.tasks.loans_due_soon_reminder",
        "schedule": crontab(hour=8, minute=0),
        "options": {"queue": "finance"},
    },
    "finance-loans-due-today": {
        "task": "apps.finance.tasks.loans_due_today_reminder",
        "schedule": crontab(hour=7, minute=30),
        "options": {"queue": "finance"},
    },
    "finance-loans-overdue-escalation": {
        "task": "apps.finance.tasks.loans_overdue_escalation",
        "schedule": crontab(hour=9, minute=0),
        "options": {"queue": "finance"},
    },
    "finance-loans-overdue-default-sweep": {
        "task": "apps.finance.tasks.loans_overdue_default_sweep",
        "schedule": crontab(hour=6, minute=0),
        "options": {"queue": "finance"},
    },
    "finance-loans-auto-penalty": {
        "task": "apps.finance.tasks.loans_auto_penalty_calculator",
        "schedule": crontab(hour=6, minute=30),
        "options": {"queue": "finance"},
    },
    "finance-contributions-penalties": {
        "task": "apps.finance.tasks.contributions_mark_overdue_and_penalize",
        "schedule": crontab(hour=20, minute=0),
        "options": {"queue": "finance"},
    },
    "finance-contribution-cycle-completion": {
        "task": "apps.finance.tasks.contributions_cycle_completion_check",
        "schedule": crontab(minute="*/30"),
        "options": {"queue": "finance"},
    },
    "finance-daily-snapshots": {
        "task": "apps.finance.tasks.finance_generate_daily_snapshots",
        "schedule": crontab(hour=23, minute=15),
        "options": {"queue": "reports"},
    },
    "finance-monthly-statements": {
        "task": "apps.finance.tasks.contributions_monthly_statement",
        "schedule": crontab(hour=7, minute=0, day_of_month=1),
        "options": {"queue": "reports"},
    },
    "payments-expire-pending-stk": {
        "task": "apps.payments.tasks.payments_expire_pending_stk",
        "schedule": crontab(minute="*/5"),
        "options": {"queue": "payments"},
    },
    "payments-reconcile-daily": {
        "task": "apps.payments.tasks.payments_daily_reconciliation",
        "schedule": crontab(hour=22, minute=0),
        "options": {"queue": "payments"},
    },
    "payments-retry-failed-b2c": {
        "task": "apps.payments.tasks.payments_retry_failed_b2c_payouts",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "payments"},
    },
    "payments-timeout-monitor": {
        "task": "apps.payments.tasks.payments_retry_timeouts",
        "schedule": crontab(minute="*/10"),
        "options": {"queue": "payments"},
    },
    "meetings-reminder-7d": {
        "task": "apps.meetings.tasks.meetings_reminder_7d",
        "schedule": crontab(hour="*/2", minute=0),
        "options": {"queue": "notifications"},
    },
    "meetings-reminder-24h": {
        "task": "apps.meetings.tasks.meetings_reminder_24h",
        "schedule": crontab(minute=0),
        "options": {"queue": "notifications"},
    },
    "meetings-reminder-1h": {
        "task": "apps.meetings.tasks.meetings_reminder_1h",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "notifications"},
    },
    "meetings-rsvp-nudge-48h": {
        "task": "apps.meetings.tasks.meetings_rsvp_nudge_48h",
        "schedule": crontab(minute="*/30"),
        "options": {"queue": "notifications"},
    },
    "meetings-minutes-compliance": {
        "task": "apps.meetings.tasks.meetings_minutes_compliance_monitor",
        "schedule": crontab(hour=7, minute=45),
        "options": {"queue": "meetings"},
    },
    "meetings-auto-schedule-next": {
        "task": "apps.meetings.tasks.meetings_auto_schedule_next",
        "schedule": crontab(hour=5, minute=0),
        "options": {"queue": "meetings"},
    },
    "governance-open-voting": {
        "task": "apps.governance.tasks.open_due_voting",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "governance"},
    },
    "governance-voting-reminders": {
        "task": "apps.governance.tasks.send_voting_reminders",
        "schedule": crontab(hour="*/2", minute=10),
        "options": {"queue": "governance"},
    },
    "governance-close-expired-voting": {
        "task": "apps.governance.tasks.close_expired_voting",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "governance"},
    },
    "governance-apply-role-changes": {
        "task": "apps.governance.tasks.apply_due_role_changes",
        "schedule": crontab(hour=0, minute=10),
        "options": {"queue": "governance"},
    },
    "issues-auto-create-system": {
        "task": "apps.issues.tasks.issues_auto_create_system",
        "schedule": crontab(hour=6, minute=15),
        "options": {"queue": "issues"},
    },
    "issues-escalate-old-open": {
        "task": "apps.issues.tasks.issues_escalate_old_open",
        "schedule": crontab(hour=6, minute=30),
        "options": {"queue": "issues"},
    },
    "issues-auto-triage-ai": {
        "task": "apps.issues.tasks.issues_auto_triage_ai",
        "schedule": crontab(hour=1, minute=0),
        "options": {"queue": "issues"},
    },
    "issues-due-reminders-auto-close": {
        "task": "apps.issues.tasks.issues_due_reminders_and_auto_close",
        "schedule": crontab(hour="*/2", minute=20),
        "options": {"queue": "issues"},
    },
    "accounts-cleanup-expired-otps": {
        "task": "apps.accounts.tasks.cleanup_expired_otps",
        "schedule": crontab(hour=2, minute=0),
        "options": {"queue": "maintenance"},
    },
    "accounts-session-cleanup": {
        "task": "apps.accounts.tasks.cleanup_stale_auth_sessions",
        "schedule": crontab(hour=2, minute=15),
        "options": {"queue": "maintenance"},
    },
    "accounts-expire-pending-membership-requests": {
        "task": "apps.accounts.tasks.expire_pending_membership_requests",
        "schedule": crontab(hour=1, minute=0),
        "options": {"queue": "maintenance"},
    },
    "accounts-pending-approval-reminders": {
        "task": "apps.accounts.tasks.send_pending_approval_reminders",
        "schedule": crontab(hour=9, minute=0),
        "options": {"queue": "notifications"},
    },
    "accounts-kyc-daily-sanctions-screening": {
        "task": "apps.accounts.tasks.kyc_daily_sanctions_screening",
        "schedule": crontab(hour=3, minute=30),
        "options": {"queue": "security"},
    },
    "accounts-kyc-renewal-reminders": {
        "task": "apps.accounts.tasks.kyc_renewal_and_expiry_reminders",
        "schedule": crontab(hour=8, minute=30),
        "options": {"queue": "notifications"},
    },
    "accounts-kyc-daily-sanctions-rescreen-v2": {
        "task": "apps.accounts.kyc.tasks.daily_sanctions_rescreen",
        "schedule": crontab(hour=4, minute=0),
        "options": {"queue": "security"},
    },
    "accounts-kyc-id-expiry-tracker-v2": {
        "task": "apps.accounts.kyc.tasks.id_expiry_tracker",
        "schedule": crontab(hour=8, minute=45),
        "options": {"queue": "notifications"},
    },
    "accounts-kyc-renewal-reminders-v2": {
        "task": "apps.accounts.kyc.tasks.annual_renewal_reminders",
        "schedule": crontab(hour=9, minute=15),
        "options": {"queue": "notifications"},
    },
    "accounts-kyc-retry-reminders-v2": {
        "task": "apps.accounts.kyc.tasks.schedule_retry_reminders",
        "schedule": crontab(minute="*/30"),
        "options": {"queue": "notifications"},
    },
    "accounts-kyc-stale-sessions-v2": {
        "task": "apps.accounts.kyc.tasks.stale_kyc_session_cleanup",
        "schedule": crontab(hour=2, minute=45),
        "options": {"queue": "maintenance"},
    },
    "maintenance-clean-temporary-files": {
        "task": "core.tasks.clean_temporary_files",
        "schedule": crontab(hour=4, minute=0),
        "options": {"queue": "maintenance"},
    },
    "maintenance-optimize-database": {
        "task": "core.tasks.optimize_database",
        "schedule": crontab(hour=3, minute=0, day_of_week=0),
        "options": {"queue": "maintenance"},
    },
    "maintenance-health-check": {
        "task": "core.tasks.health_check",
        "schedule": crontab(minute="*/5"),
        "options": {"queue": "monitoring"},
    },
    "maintenance-backup-snapshot": {
        "task": "core.tasks.create_backup_snapshot",
        "schedule": crontab(hour=1, minute=30),
        "options": {"queue": "maintenance"},
    },
    "reports-process-scheduled": {
        "task": "apps.reports.tasks.process_scheduled_reports",
        "schedule": crontab(minute="*/10"),
        "options": {"queue": "reports"},
    },
    "reports-cleanup-old": {
        "task": "apps.reports.tasks.cleanup_old_reports",
        "schedule": crontab(hour=2, minute=30, day_of_week=0),
        "options": {"queue": "reports"},
    },
}

CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_TIMEZONE = settings.TIME_ZONE
