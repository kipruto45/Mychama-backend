from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai.models import AIToolCallLog
from apps.ai.tasks import ai_membership_risk_scoring_task
from apps.automations.models import JobRun, JobRunStatus
from apps.automations.services import AutomationJobRunner, AutomationService
from apps.chama.models import Chama, Membership, MembershipRole
from apps.finance.models import (
    InstallmentSchedule,
    InstallmentStatus,
    LedgerDirection,
    LedgerEntry,
    LedgerEntryType,
    Loan,
    LoanInterestType,
    LoanStatus,
)
from apps.finance.tasks import ledger_daily_integrity_audit, loans_delinquency_monitor
from apps.payments.models import PaymentIntent, PaymentIntentStatus, PaymentIntentType, PaymentPurpose
from apps.payments.tasks import payments_advanced_reconciliation
from apps.notifications.models import Notification, NotificationPriority, NotificationStatus, NotificationType
from apps.notifications.tasks import behavioral_notification_throttle

pytestmark = pytest.mark.django_db



def create_user(phone: str, name: str) -> User:
    return User.objects.create_user(
        phone=phone,
        password="SecurePass123!",
        full_name=name,
    )



def create_setup():
    admin = create_user("+254722100001", "AI Admin")
    member = create_user("+254722100002", "AI Member")

    chama = Chama.objects.create(
        name="AI Automations Chama",
        description="Test chama",
        county="Nairobi",
        subcounty="Westlands",
        created_by=admin,
        updated_by=admin,
    )
    Membership.objects.create(
        user=admin,
        chama=chama,
        role=MembershipRole.CHAMA_ADMIN,
        is_active=True,
        is_approved=True,
        approved_by=admin,
        created_by=admin,
        updated_by=admin,
    )
    Membership.objects.create(
        user=member,
        chama=chama,
        role=MembershipRole.MEMBER,
        is_active=True,
        is_approved=True,
        approved_by=admin,
        created_by=admin,
        updated_by=admin,
    )

    return {"admin": admin, "member": member, "chama": chama}



def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client



def test_ai_chat_logs_tool_usage_for_member():
    setup = create_setup()
    response = auth_client(setup["member"]).post(
        "/api/v1/ai/chat",
        {
            "chama_id": str(setup["chama"].id),
            "mode": "member_assistant",
            "message": "My contributions this month",
        },
        format="json",
    )

    assert response.status_code == 200
    assert "conversation_id" in response.json()
    assert AIToolCallLog.objects.filter(chama=setup["chama"]).exists()



def test_ai_status_endpoint_is_available_for_authenticated_user():
    setup = create_setup()
    response = auth_client(setup["member"]).get("/api/v1/ai/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "operational"
    assert "chat_model" in payload
    assert payload["features"]["chat"] is True


def test_member_cannot_use_admin_only_ai_tooling():
    setup = create_setup()

    response = auth_client(setup["member"]).post(
        "/api/v1/ai/chat",
        {
            "chama_id": str(setup["chama"].id),
            "mode": "member_assistant",
            "message": "Who is overdue in this chama?",
        },
        format="json",
    )

    assert response.status_code in {400, 403}



def test_admin_can_use_overdue_ai_tooling():
    setup = create_setup()

    response = auth_client(setup["admin"]).post(
        "/api/v1/ai/chat",
        {
            "chama_id": str(setup["chama"].id),
            "mode": "admin_assistant",
            "message": "Who is overdue this week?",
        },
        format="json",
    )

    assert response.status_code == 200
    assert "tool_usage" in response.json()



def test_automation_job_runner_creates_jobrun_record():
    result = AutomationJobRunner.run_job(
        name="test_job_runner",
        schedule="manual",
        description="test",
        callback=lambda: {"ok": True},
    )

    assert result["status"] == "success"
    run = JobRun.objects.get(id=result["run_id"])
    assert run.status == JobRunStatus.SUCCESS
    assert run.meta == {"ok": True}



def test_automation_quiet_hours_blocks_sms(monkeypatch):
    monkeypatch.setattr(AutomationService, "is_quiet_hours", staticmethod(lambda now=None: True))

    allowed, reason = AutomationService.should_send_notification(
        user_id="u1",
        chama_id="c1",
        channel="sms",
    )

    assert allowed is False
    assert reason == "quiet_hours"


def test_ledger_integrity_audit_flags_reversal_mismatch():
    setup = create_setup()
    admin = setup["admin"]
    chama = setup["chama"]

    original = LedgerEntry.objects.create(
        chama=chama,
        entry_type=LedgerEntryType.CONTRIBUTION,
        direction=LedgerDirection.CREDIT,
        amount=Decimal("1000.00"),
        currency="KES",
        idempotency_key=f"test-ledger-original:{chama.id}",
        narration="Original entry",
        created_by=admin,
        updated_by=admin,
    )
    LedgerEntry.objects.create(
        chama=chama,
        entry_type=LedgerEntryType.ADJUSTMENT,
        direction=LedgerDirection.CREDIT,  # wrong on purpose: should be opposite direction.
        amount=Decimal("1000.00"),
        currency="KES",
        idempotency_key=f"test-ledger-reversal:{chama.id}",
        narration="Bad reversal",
        reversal_of=original,
        created_by=admin,
        updated_by=admin,
    )

    result = ledger_daily_integrity_audit(chama_id=str(chama.id))
    assert result["status"] == "success"
    assert result["result"]["flagged_chamas"] >= 1


def test_loans_delinquency_monitor_outputs_bucket_and_par():
    setup = create_setup()
    admin = setup["admin"]
    member = setup["member"]
    chama = setup["chama"]

    loan = Loan.objects.create(
        chama=chama,
        member=member,
        principal=Decimal("12000.00"),
        interest_type=LoanInterestType.FLAT,
        interest_rate=Decimal("10.00"),
        duration_months=6,
        status=LoanStatus.ACTIVE,
        created_by=admin,
        updated_by=admin,
    )
    InstallmentSchedule.objects.create(
        loan=loan,
        due_date=timezone.localdate() - timedelta(days=40),
        expected_amount=Decimal("2500.00"),
        expected_principal=Decimal("2000.00"),
        expected_interest=Decimal("500.00"),
        status=InstallmentStatus.OVERDUE,
        created_by=admin,
        updated_by=admin,
    )

    result = loans_delinquency_monitor(chama_id=str(chama.id))
    assert result["status"] == "success"
    payload = result["result"]
    assert payload["bucket_summary"]["dpd_31_60"]["count"] >= 1
    assert Decimal(payload["par30_percent"]) > Decimal("0.00")


def test_payments_advanced_reconciliation_detects_missing_provider_match():
    setup = create_setup()
    member = setup["member"]
    chama = setup["chama"]
    PaymentIntent.objects.create(
        chama=chama,
        created_by=member,
        intent_type=PaymentIntentType.DEPOSIT,
        purpose=PaymentPurpose.CONTRIBUTION,
        amount=Decimal("350.00"),
        currency="KES",
        phone=member.phone,
        status=PaymentIntentStatus.SUCCESS,
        idempotency_key=f"adv-recon:{chama.id}:{member.id}",
        metadata={},
    )

    result = payments_advanced_reconciliation(
        chama_id=str(chama.id),
        run_date=timezone.localdate().isoformat(),
    )
    assert result["status"] == "success"
    assert result["result"]["missing_provider_side"] >= 1


def test_ai_membership_risk_scoring_task_runs_for_active_chama():
    setup = create_setup()
    result = ai_membership_risk_scoring_task(chama_id=str(setup["chama"].id))
    assert result["status"] == "success"
    assert result["result"]["generated"] >= 1


def test_behavioral_notification_throttle_delays_non_urgent_pending():
    setup = create_setup()
    user = setup["member"]
    chama = setup["chama"]

    for idx in range(22):
        Notification.objects.create(
            chama=chama,
            recipient=user,
            type=NotificationType.SYSTEM,
            priority=NotificationPriority.NORMAL,
            status=NotificationStatus.SENT,
            message=f"noise-{idx}",
        )

    pending = Notification.objects.create(
        chama=chama,
        recipient=user,
        type=NotificationType.SYSTEM,
        priority=NotificationPriority.NORMAL,
        status=NotificationStatus.PENDING,
        message="pending-message",
    )
    assert pending.scheduled_at is None

    result = behavioral_notification_throttle()
    assert result["users_flagged"] >= 1
    pending.refresh_from_db()
    assert pending.scheduled_at is not None
