from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MembershipRole
from apps.finance.models import (
    Contribution,
    ContributionType,
    InstallmentSchedule,
    InstallmentStatus,
    Loan,
    LoanInterestType,
    LoanStatus,
)
from apps.notifications.models import Notification, NotificationType
from apps.notifications.services import NotificationService
from apps.notifications.tasks import daily_due_reminders

pytestmark = pytest.mark.django_db


def create_user(phone: str, full_name: str) -> User:
    return User.objects.create_user(
        phone=phone,
        password="SecurePass123!",
        full_name=full_name,
        email=f"{phone[1:]}@example.com",
    )


def test_daily_due_reminders_selects_correct_due_items(monkeypatch):
    monkeypatch.setattr(NotificationService, "queue_notification", lambda _: None)

    admin = create_user("+254704000001", "Notifications Admin")
    due_member = create_user("+254704000002", "Due Member")
    paid_member = create_user("+254704000003", "Paid Member")

    chama = Chama.objects.create(
        name="Notifications Test Chama",
        description="Notification task coverage",
        county="Nairobi",
        subcounty="Westlands",
        created_by=admin,
        updated_by=admin,
    )

    Membership.objects.create(
        user=due_member,
        chama=chama,
        role=MembershipRole.MEMBER,
        is_active=True,
        is_approved=True,
        approved_by=admin,
        created_by=admin,
        updated_by=admin,
    )
    Membership.objects.create(
        user=paid_member,
        chama=chama,
        role=MembershipRole.MEMBER,
        is_active=True,
        is_approved=True,
        approved_by=admin,
        created_by=admin,
        updated_by=admin,
    )

    contribution_type = ContributionType.objects.create(
        chama=chama,
        name="Monthly Savings",
        frequency="monthly",
        default_amount="1000.00",
        is_active=True,
        created_by=admin,
        updated_by=admin,
    )

    Contribution.objects.create(
        chama=chama,
        member=paid_member,
        contribution_type=contribution_type,
        amount="1000.00",
        date_paid=timezone.localdate(),
        method="mpesa",
        receipt_code="NTF-PAID-001",
        recorded_by=admin,
        created_by=admin,
        updated_by=admin,
    )

    loan = Loan.objects.create(
        chama=chama,
        member=due_member,
        principal="5000.00",
        interest_type=LoanInterestType.FLAT,
        interest_rate="12.00",
        duration_months=6,
        status=LoanStatus.ACTIVE,
        created_by=admin,
        updated_by=admin,
    )

    InstallmentSchedule.objects.create(
        loan=loan,
        due_date=timezone.localdate(),
        expected_amount="900.00",
        status=InstallmentStatus.DUE,
        created_by=admin,
        updated_by=admin,
    )
    InstallmentSchedule.objects.create(
        loan=loan,
        due_date=timezone.localdate() + timedelta(days=1),
        expected_amount="900.00",
        status=InstallmentStatus.DUE,
        created_by=admin,
        updated_by=admin,
    )
    InstallmentSchedule.objects.create(
        loan=loan,
        due_date=timezone.localdate(),
        expected_amount="900.00",
        status=InstallmentStatus.PAID,
        created_by=admin,
        updated_by=admin,
    )

    result = daily_due_reminders()

    assert result["contribution_reminders"] == 1
    assert result["loan_installment_reminders"] == 1

    contribution_notifications = Notification.objects.filter(
        type=NotificationType.CONTRIBUTION_REMINDER
    )
    loan_notifications = Notification.objects.filter(type=NotificationType.LOAN_UPDATE)

    assert contribution_notifications.count() == 1
    assert loan_notifications.count() == 1
    assert contribution_notifications.first().recipient_id == due_member.id
    assert loan_notifications.first().recipient_id == due_member.id
