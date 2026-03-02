from __future__ import annotations

# ruff: noqa: E402
import os
import pathlib
import sys
from datetime import timedelta
from decimal import Decimal

import django

# Ensure project root is on sys.path so `import config` works when running
# this script directly.
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    os.getenv("DJANGO_SETTINGS_MODULE", "config.settings.development"),
)
django.setup()

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import UserPreference
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.finance.models import (
    Contribution,
    ContributionFrequency,
    ContributionType,
    InstallmentSchedule,
    InstallmentStatus,
    LedgerDirection,
    LedgerEntry,
    LedgerEntryType,
    Loan,
    LoanEligibilityStatus,
    LoanInterestType,
    LoanPenaltyType,
    LoanProduct,
    LoanStatus,
    Penalty,
    PenaltyStatus,
    Repayment,
)
from apps.issues.models import Issue, IssueCategory, IssuePriority, IssueStatus
from apps.meetings.models import (
    Attendance,
    AttendanceStatus,
    Meeting,
    Resolution,
    ResolutionStatus,
)
from apps.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationInboxStatus,
    NotificationPriority,
    NotificationStatus,
    NotificationType,
)
from apps.payments.models import (
    PaymentIntent,
    PaymentIntentStatus,
    PaymentIntentType,
    PaymentPurpose,
    PaymentReconciliationRun,
    ReconciliationRunStatus,
)
from core.constants import CurrencyChoices, MethodChoices

User = get_user_model()

SEED_PASSWORD = "SeedPass123!"
SUPERADMIN_PHONE = "+254711700001"

ROLE_USERS = {
    MembershipRole.CHAMA_ADMIN: ("+254711700010", "Chama Admin"),
    MembershipRole.TREASURER: ("+254711700011", "Treasurer"),
    MembershipRole.SECRETARY: ("+254711700012", "Secretary"),
    MembershipRole.AUDITOR: ("+254711700013", "Auditor"),
    MembershipRole.ADMIN: ("+254711700015", "Admin"),
}

MEMBER_USERS = [
    ("+254711700101", "Member One"),
    ("+254711700102", "Member Two"),
    ("+254711700103", "Member Three"),
]


def print_line(label: str, value: str) -> None:
    print(f"[seed] {label}: {value}")


def ensure_user(
    *,
    phone: str,
    full_name: str,
    is_superuser: bool = False,
) -> tuple[User, bool]:
    user = User.objects.filter(phone=phone).first()
    if user:
        return user, False

    if is_superuser:
        created = User.objects.create_superuser(
            phone=phone,
            password=SEED_PASSWORD,
            full_name=full_name,
            email=f"{phone.replace('+', '')}@seed.local",
        )
        return created, True

    created = User.objects.create_user(
        phone=phone,
        password=SEED_PASSWORD,
        full_name=full_name,
        email=f"{phone.replace('+', '')}@seed.local",
    )
    return created, True


def ensure_membership(
    *,
    chama: Chama,
    user: User,
    role: str,
    approver: User,
) -> Membership:
    membership, created = Membership.objects.get_or_create(
        chama=chama,
        user=user,
        defaults={
            "role": role,
            "status": MemberStatus.ACTIVE,
            "is_active": True,
            "is_approved": True,
            "joined_at": timezone.now(),
            "approved_at": timezone.now(),
            "approved_by": approver,
            "created_by": approver,
            "updated_by": approver,
        },
    )
    if not created:
        membership.role = role
        membership.status = MemberStatus.ACTIVE
        membership.is_active = True
        membership.is_approved = True
        membership.approved_at = membership.approved_at or timezone.now()
        membership.approved_by = approver
        membership.updated_by = approver
        membership.save(
            update_fields=[
                "role",
                "status",
                "is_active",
                "is_approved",
                "approved_at",
                "approved_by",
                "updated_by",
                "updated_at",
            ]
        )
    return membership


def ensure_preference(*, user: User, active_chama: Chama) -> None:
    UserPreference.objects.update_or_create(
        user=user,
        defaults={
            "active_chama": active_chama,
            "low_data_mode": False,
            "ussd_enabled": True,
            "prefer_sms": True,
            "prefer_email": True,
            "prefer_in_app": True,
        },
    )


def ensure_notification(
    *,
    chama: Chama,
    recipient: User,
    notification_type: str,
    category: str,
    subject: str,
    message: str,
    idempotency_key: str,
    actor: User,
) -> None:
    Notification.objects.update_or_create(
        idempotency_key=idempotency_key,
        defaults={
            "chama": chama,
            "recipient": recipient,
            "type": notification_type,
            "category": category,
            "priority": NotificationPriority.NORMAL,
            "status": NotificationStatus.SENT,
            "inbox_status": NotificationInboxStatus.UNREAD,
            "subject": subject,
            "message": message,
            "send_email": False,
            "send_sms": False,
            "send_push": False,
            "created_by": actor,
            "updated_by": actor,
        },
    )


@transaction.atomic
def seed() -> None:
    now = timezone.now()
    today = timezone.localdate()

    print_line("starting", "Digital Chama seed")

    superadmin, created = ensure_user(
        phone=SUPERADMIN_PHONE,
        full_name="System Super Admin",
        is_superuser=True,
    )
    print_line(
        "superadmin", f"{superadmin.phone} ({'created' if created else 'existing'})"
    )

    primary_chama, _ = Chama.objects.update_or_create(
        name="Digital Chama HQ",
        defaults={
            "description": "Primary seeded chama for dashboard and workflow QA.",
            "county": "Nairobi",
            "subcounty": "Westlands",
            "currency": CurrencyChoices.KES,
            "status": "active",
            "created_by": superadmin,
            "updated_by": superadmin,
        },
    )
    secondary_chama, _ = Chama.objects.update_or_create(
        name="Digital Chama Branch",
        defaults={
            "description": "Secondary chama for multi-chama switch testing.",
            "county": "Nakuru",
            "subcounty": "Naivasha",
            "currency": CurrencyChoices.KES,
            "status": "active",
            "created_by": superadmin,
            "updated_by": superadmin,
        },
    )
    print_line("chamas", f"{primary_chama.name}, {secondary_chama.name}")

    seeded_users: dict[str, User] = {}
    for role, (phone, full_name) in ROLE_USERS.items():
        user, role_created = ensure_user(phone=phone, full_name=full_name)
        seeded_users[role] = user
        ensure_membership(
            chama=primary_chama,
            user=user,
            role=role,
            approver=superadmin,
        )
        ensure_preference(user=user, active_chama=primary_chama)
        print_line(
            "role_user",
            f"{role} -> {user.phone} ({'created' if role_created else 'existing'})",
        )

    member_users: list[User] = []
    for phone, full_name in MEMBER_USERS:
        member_user, member_created = ensure_user(phone=phone, full_name=full_name)
        member_users.append(member_user)
        ensure_membership(
            chama=primary_chama,
            user=member_user,
            role=MembershipRole.MEMBER,
            approver=seeded_users[MembershipRole.CHAMA_ADMIN],
        )
        ensure_preference(user=member_user, active_chama=primary_chama)
        print_line(
            "member_user",
            f"{member_user.phone} ({'created' if member_created else 'existing'})",
        )

    # Add one member to secondary chama for multi-chama switching.
    ensure_membership(
        chama=secondary_chama,
        user=member_users[0],
        role=MembershipRole.MEMBER,
        approver=seeded_users[MembershipRole.CHAMA_ADMIN],
    )

    contribution_type, _ = ContributionType.objects.update_or_create(
        chama=primary_chama,
        name="Regular Savings",
        defaults={
            "frequency": ContributionFrequency.MONTHLY,
            "default_amount": Decimal("2000.00"),
            "is_active": True,
            "created_by": seeded_users[MembershipRole.TREASURER],
            "updated_by": seeded_users[MembershipRole.TREASURER],
        },
    )

    contribution, _ = Contribution.objects.get_or_create(
        receipt_code=f"SEED-CONTR-{primary_chama.id.hex[:8]}-001",
        defaults={
            "chama": primary_chama,
            "member": member_users[0],
            "contribution_type": contribution_type,
            "amount": Decimal("2000.00"),
            "date_paid": today - timedelta(days=2),
            "method": MethodChoices.MPESA,
            "recorded_by": seeded_users[MembershipRole.TREASURER],
            "created_by": seeded_users[MembershipRole.TREASURER],
            "updated_by": seeded_users[MembershipRole.TREASURER],
        },
    )

    loan_product, _ = LoanProduct.objects.update_or_create(
        chama=primary_chama,
        name="Standard Loan Product",
        defaults={
            "is_active": True,
            "is_default": True,
            "max_loan_amount": Decimal("50000.00"),
            "contribution_multiple": Decimal("3.00"),
            "interest_type": LoanInterestType.FLAT,
            "interest_rate": Decimal("5.00"),
            "min_duration_months": 1,
            "max_duration_months": 12,
            "grace_period_days": 3,
            "late_penalty_type": LoanPenaltyType.FIXED,
            "late_penalty_value": Decimal("100.00"),
            "minimum_membership_months": 2,
            "minimum_contribution_months": 2,
            "created_by": seeded_users[MembershipRole.CHAMA_ADMIN],
            "updated_by": seeded_users[MembershipRole.CHAMA_ADMIN],
        },
    )

    loan = Loan.objects.filter(
        chama=primary_chama,
        member=member_users[0],
        status=LoanStatus.ACTIVE,
        principal=Decimal("12000.00"),
    ).first()
    if loan is None:
        loan = Loan.objects.create(
            chama=primary_chama,
            member=member_users[0],
            loan_product=loan_product,
            principal=Decimal("12000.00"),
            interest_type=LoanInterestType.FLAT,
            interest_rate=Decimal("5.00"),
            duration_months=6,
            grace_period_days=3,
            late_penalty_type=LoanPenaltyType.FIXED,
            late_penalty_value=Decimal("100.00"),
            early_repayment_discount_percent=Decimal("0.00"),
            eligibility_status=LoanEligibilityStatus.ELIGIBLE,
            recommended_max_amount=Decimal("24000.00"),
            status=LoanStatus.ACTIVE,
            approved_at=now - timedelta(days=14),
            approved_by=seeded_users[MembershipRole.CHAMA_ADMIN],
            disbursed_at=now - timedelta(days=12),
            disbursed_by=seeded_users[MembershipRole.TREASURER],
            disbursement_reference=f"SEED-LOAN-DISB-{primary_chama.id.hex[:8]}",
            created_by=seeded_users[MembershipRole.CHAMA_ADMIN],
            updated_by=seeded_users[MembershipRole.CHAMA_ADMIN],
        )

    for due_offset, status, amount in [
        (-5, InstallmentStatus.OVERDUE, Decimal("2100.00")),
        (25, InstallmentStatus.DUE, Decimal("2100.00")),
        (55, InstallmentStatus.DUE, Decimal("2100.00")),
    ]:
        due_date = today + timedelta(days=due_offset)
        InstallmentSchedule.objects.get_or_create(
            loan=loan,
            due_date=due_date,
            defaults={
                "expected_amount": amount,
                "expected_principal": Decimal("2000.00"),
                "expected_interest": Decimal("100.00"),
                "expected_penalty": Decimal("0.00"),
                "status": status,
                "created_by": seeded_users[MembershipRole.TREASURER],
                "updated_by": seeded_users[MembershipRole.TREASURER],
            },
        )

    Repayment.objects.get_or_create(
        receipt_code=f"SEED-REPAY-{primary_chama.id.hex[:8]}-001",
        defaults={
            "loan": loan,
            "amount": Decimal("2100.00"),
            "date_paid": today - timedelta(days=1),
            "method": MethodChoices.MPESA,
            "recorded_by": seeded_users[MembershipRole.TREASURER],
            "created_by": seeded_users[MembershipRole.TREASURER],
            "updated_by": seeded_users[MembershipRole.TREASURER],
        },
    )

    Penalty.objects.update_or_create(
        chama=primary_chama,
        member=member_users[0],
        reason="Late installment sample penalty",
        defaults={
            "amount": Decimal("150.00"),
            "due_date": today + timedelta(days=7),
            "status": PenaltyStatus.UNPAID,
            "issued_by": seeded_users[MembershipRole.CHAMA_ADMIN],
            "created_by": seeded_users[MembershipRole.CHAMA_ADMIN],
            "updated_by": seeded_users[MembershipRole.CHAMA_ADMIN],
        },
    )

    for (
        idempotency_key,
        entry_type,
        direction,
        amount,
        reference_type,
        reference_id,
        narration,
    ) in [
        (
            f"seed-ledger-contribution-{primary_chama.id.hex[:8]}",
            LedgerEntryType.CONTRIBUTION,
            LedgerDirection.CREDIT,
            Decimal("2000.00"),
            "contribution",
            contribution.id,
            "Seed contribution posting",
        ),
        (
            f"seed-ledger-loan-disbursement-{primary_chama.id.hex[:8]}",
            LedgerEntryType.LOAN_DISBURSEMENT,
            LedgerDirection.DEBIT,
            Decimal("12000.00"),
            "loan",
            loan.id,
            "Seed loan disbursement",
        ),
        (
            f"seed-ledger-loan-repayment-{primary_chama.id.hex[:8]}",
            LedgerEntryType.REPAYMENT,
            LedgerDirection.CREDIT,
            Decimal("2100.00"),
            "loan",
            loan.id,
            "Seed loan repayment",
        ),
    ]:
        LedgerEntry.objects.update_or_create(
            chama=primary_chama,
            idempotency_key=idempotency_key,
            defaults={
                "entry_type": entry_type,
                "direction": direction,
                "amount": amount,
                "currency": CurrencyChoices.KES,
                "reference_type": reference_type,
                "reference_id": reference_id,
                "narration": narration,
                "created_by": seeded_users[MembershipRole.TREASURER],
                "updated_by": seeded_users[MembershipRole.TREASURER],
            },
        )

    PaymentIntent.objects.update_or_create(
        chama=primary_chama,
        idempotency_key=f"seed-payment-deposit-{primary_chama.id.hex[:8]}",
        defaults={
            "intent_type": PaymentIntentType.DEPOSIT,
            "purpose": PaymentPurpose.CONTRIBUTION,
            "reference_type": "CONTRIBUTION_TYPE",
            "reference_id": contribution_type.id,
            "amount": Decimal("2000.00"),
            "currency": CurrencyChoices.KES,
            "phone": member_users[0].phone,
            "status": PaymentIntentStatus.SUCCESS,
            "expires_at": now + timedelta(minutes=15),
            "metadata": {"seed": True, "channel": "STK"},
            "created_by": member_users[0],
            "updated_by": member_users[0],
        },
    )
    PaymentIntent.objects.update_or_create(
        chama=primary_chama,
        idempotency_key=f"seed-payment-loan-repay-{primary_chama.id.hex[:8]}",
        defaults={
            "intent_type": PaymentIntentType.LOAN_REPAYMENT,
            "purpose": PaymentPurpose.LOAN_REPAYMENT,
            "reference_type": "LOAN",
            "reference_id": loan.id,
            "amount": Decimal("2100.00"),
            "currency": CurrencyChoices.KES,
            "phone": member_users[0].phone,
            "status": PaymentIntentStatus.SUCCESS,
            "expires_at": now + timedelta(minutes=15),
            "metadata": {"seed": True, "channel": "C2B"},
            "created_by": member_users[0],
            "updated_by": member_users[0],
        },
    )
    PaymentIntent.objects.update_or_create(
        chama=primary_chama,
        idempotency_key=f"seed-payment-loan-disbursement-{primary_chama.id.hex[:8]}",
        defaults={
            "intent_type": PaymentIntentType.LOAN_DISBURSEMENT,
            "purpose": PaymentPurpose.OTHER,
            "reference_type": "LOAN",
            "reference_id": loan.id,
            "amount": Decimal("12000.00"),
            "currency": CurrencyChoices.KES,
            "phone": member_users[0].phone,
            "status": PaymentIntentStatus.PENDING,
            "expires_at": now + timedelta(hours=24),
            "metadata": {"seed": True, "queue": "pending_disbursement"},
            "created_by": seeded_users[MembershipRole.TREASURER],
            "updated_by": seeded_users[MembershipRole.TREASURER],
        },
    )

    PaymentReconciliationRun.objects.update_or_create(
        chama=primary_chama,
        run_at=now.replace(hour=22, minute=0, second=0, microsecond=0),
        defaults={
            "status": ReconciliationRunStatus.SUCCESS,
            "totals": {"success": 2, "pending": 1, "failed": 0},
            "anomalies": {},
            "created_by": seeded_users[MembershipRole.TREASURER],
            "updated_by": seeded_users[MembershipRole.TREASURER],
        },
    )

    meeting = Meeting.objects.filter(
        chama=primary_chama,
        title="Monthly Finance Review",
    ).first()
    if meeting is None:
        meeting = Meeting.objects.create(
            chama=primary_chama,
            title="Monthly Finance Review",
            date=now + timedelta(days=2),
            agenda="Review contributions, loans, and penalties.",
            created_by=seeded_users[MembershipRole.SECRETARY],
            updated_by=seeded_users[MembershipRole.SECRETARY],
        )
    Attendance.objects.update_or_create(
        meeting=meeting,
        member=member_users[0],
        defaults={
            "status": AttendanceStatus.PRESENT,
            "notes": "Seeded attendance record",
            "created_by": seeded_users[MembershipRole.SECRETARY],
            "updated_by": seeded_users[MembershipRole.SECRETARY],
        },
    )
    Resolution.objects.update_or_create(
        meeting=meeting,
        text="Follow up with overdue members before next meeting.",
        defaults={
            "assigned_to": seeded_users[MembershipRole.SECRETARY],
            "due_date": today + timedelta(days=14),
            "status": ResolutionStatus.OPEN,
            "created_by": seeded_users[MembershipRole.SECRETARY],
            "updated_by": seeded_users[MembershipRole.SECRETARY],
        },
    )

    issue, _ = Issue.objects.get_or_create(
        chama=primary_chama,
        title="Repayment allocation clarification",
        defaults={
            "description": "Member requests clarification on repayment allocation.",
            "category": IssueCategory.FINANCE,
            "priority": IssuePriority.HIGH,
            "status": IssueStatus.IN_REVIEW,
            "assigned_to": seeded_users[MembershipRole.SECRETARY],
            "created_by": member_users[0],
            "updated_by": seeded_users[MembershipRole.SECRETARY],
        },
    )
    if issue.status != IssueStatus.IN_REVIEW:
        issue.status = IssueStatus.IN_REVIEW
        issue.updated_by = seeded_users[MembershipRole.SECRETARY]
        issue.save(update_fields=["status", "updated_by", "updated_at"])

    notifications_seed = [
        (
            seeded_users[MembershipRole.CHAMA_ADMIN],
            NotificationType.SYSTEM,
            NotificationCategory.SYSTEM,
            "Admin dashboard ready",
            "Seed data has been loaded for admin dashboard checks.",
            "admin",
        ),
        (
            seeded_users[MembershipRole.TREASURER],
            NotificationType.PAYMENT_CONFIRMATION,
            NotificationCategory.PAYMENTS,
            "Repayment received",
            "A loan repayment was posted successfully.",
            "treasurer",
        ),
        (
            seeded_users[MembershipRole.SECRETARY],
            NotificationType.MEETING_NOTIFICATION,
            NotificationCategory.MEETINGS,
            "Meeting scheduled",
            "Monthly Finance Review meeting is in 2 days.",
            "secretary",
        ),
        (
            seeded_users[MembershipRole.AUDITOR],
            NotificationType.SYSTEM,
            NotificationCategory.SYSTEM,
            "Reconciliation complete",
            "Daily payment reconciliation completed successfully.",
            "auditor",
        ),
        (
            member_users[0],
            NotificationType.LOAN_UPDATE,
            NotificationCategory.LOANS,
            "Loan due soon",
            "Your next installment is due soon. Open Pay Loan to settle.",
            "member",
        ),
    ]
    for recipient, n_type, category, subject, message, suffix in notifications_seed:
        ensure_notification(
            chama=primary_chama,
            recipient=recipient,
            notification_type=n_type,
            category=category,
            subject=subject,
            message=message,
            idempotency_key=f"seed-notification-{primary_chama.id.hex[:8]}-{suffix}",
            actor=seeded_users[MembershipRole.CHAMA_ADMIN],
        )

    print_line("summary_users", str(User.objects.count()))
    print_line("summary_memberships", str(Membership.objects.count()))
    print_line("summary_notifications", str(Notification.objects.count()))
    print_line("summary_meetings", str(Meeting.objects.count()))
    print_line("summary_issues", str(Issue.objects.count()))
    print_line("summary_payment_intents", str(PaymentIntent.objects.count()))
    print_line("summary_ledger_entries", str(LedgerEntry.objects.count()))
    print_line("credentials", f"default password for seeded users: {SEED_PASSWORD}")
    print_line("done", "Seed completed successfully.")


if __name__ == "__main__":
    seed()
