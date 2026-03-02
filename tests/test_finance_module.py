from datetime import date

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MembershipRole
from apps.finance.models import ContributionType, LedgerEntry, Loan, LoanProduct

pytestmark = pytest.mark.django_db


def create_user(phone: str, full_name: str) -> User:
    return User.objects.create_user(
        phone=phone,
        password="SecurePass123!",
        full_name=full_name,
        email=f"{phone[1:]}@example.com",
    )


def auth_client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def create_chama_with_memberships():
    admin = create_user("+254701000001", "Finance Admin")
    treasurer = create_user("+254701000002", "Finance Treasurer")
    member = create_user("+254701000003", "Finance Member")

    chama = Chama.objects.create(
        name="Finance Test Chama",
        description="Finance module tests",
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
        user=treasurer,
        chama=chama,
        role=MembershipRole.TREASURER,
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

    LoanProduct.objects.create(
        chama=chama,
        name="Default Policy",
        is_active=True,
        is_default=True,
        max_loan_amount="50000.00",
        contribution_multiple="0.00",
        interest_type="flat",
        interest_rate="12.00",
        min_duration_months=1,
        max_duration_months=12,
        grace_period_days=0,
        late_penalty_type="fixed",
        late_penalty_value="100.00",
        minimum_membership_months=0,
        minimum_contribution_months=0,
        block_if_unpaid_penalties=True,
        block_if_overdue_loans=True,
        require_treasurer_review=True,
        require_separate_disburser=False,
        created_by=admin,
        updated_by=admin,
    )

    return chama, admin, treasurer, member


def test_posting_contribution_creates_ledger_entry():
    chama, _, treasurer, member = create_chama_with_memberships()
    contribution_type = ContributionType.objects.create(
        chama=chama,
        name="Monthly",
        frequency="monthly",
        default_amount="1000.00",
        created_by=treasurer,
        updated_by=treasurer,
    )

    client = auth_client(treasurer)
    response = client.post(
        "/api/v1/finance/contributions/record",
        {
            "chama_id": str(chama.id),
            "member_id": str(member.id),
            "contribution_type_id": str(contribution_type.id),
            "amount": "1200.00",
            "date_paid": str(date.today()),
            "method": "mpesa",
            "receipt_code": "RCP-001",
            "idempotency_key": "contrib-finance-1",
        },
        format="json",
    )

    assert response.status_code == 201
    assert LedgerEntry.objects.filter(
        chama=chama,
        entry_type="contribution",
        idempotency_key="contrib-finance-1",
    ).exists()


def test_duplicate_idempotency_key_rejected():
    chama, _, treasurer, member = create_chama_with_memberships()
    contribution_type = ContributionType.objects.create(
        chama=chama,
        name="Monthly2",
        frequency="monthly",
        default_amount="1000.00",
        created_by=treasurer,
        updated_by=treasurer,
    )

    payload = {
        "chama_id": str(chama.id),
        "member_id": str(member.id),
        "contribution_type_id": str(contribution_type.id),
        "amount": "500.00",
        "date_paid": str(date.today()),
        "method": "mpesa",
        "receipt_code": "RCP-002",
        "idempotency_key": "same-key-001",
    }

    client = auth_client(treasurer)
    first = client.post("/api/v1/finance/contributions/record", payload, format="json")
    second = client.post(
        "/api/v1/finance/contributions/record",
        {**payload, "receipt_code": "RCP-003"},
        format="json",
    )

    assert first.status_code == 201
    assert second.status_code == 409


def test_loan_flow_end_to_end_creates_schedule_and_ledger_entries():
    chama, admin, treasurer, member = create_chama_with_memberships()

    member_client = auth_client(member)
    admin_client = auth_client(admin)
    treasurer_client = auth_client(treasurer)

    request_resp = member_client.post(
        "/api/v1/finance/loans/request",
        {
            "chama_id": str(chama.id),
            "principal": "10000.00",
            "duration_months": 6,
        },
        format="json",
    )
    assert request_resp.status_code == 201

    loan_id = request_resp.json()["id"]

    review_resp = treasurer_client.post(
        f"/api/v1/finance/loans/{loan_id}/review",
        {"decision": "approved", "note": "Funds available"},
        format="json",
    )
    assert review_resp.status_code == 200

    approve_resp = admin_client.post(
        f"/api/v1/finance/loans/{loan_id}/approve",
        {"note": "Approved by admin"},
        format="json",
    )
    assert approve_resp.status_code == 200

    disburse_resp = admin_client.post(f"/api/v1/finance/loans/{loan_id}/disburse")
    assert disburse_resp.status_code == 200

    repay_resp = treasurer_client.post(
        f"/api/v1/finance/loans/{loan_id}/repay",
        {
            "amount": "2000.00",
            "date_paid": str(date.today()),
            "method": "mpesa",
            "receipt_code": "RPY-001",
            "idempotency_key": "repay-key-001",
        },
        format="json",
    )
    assert repay_resp.status_code == 201

    loan = Loan.objects.get(id=loan_id)
    assert loan.installments.count() > 0
    assert LedgerEntry.objects.filter(
        reference_type="Loan", reference_id=loan.id
    ).exists()
    assert LedgerEntry.objects.filter(reference_type="Repayment").exists()


def test_permissions_member_cannot_record_contribution():
    chama, _, treasurer, member = create_chama_with_memberships()
    contribution_type = ContributionType.objects.create(
        chama=chama,
        name="Monthly3",
        frequency="monthly",
        default_amount="1000.00",
        created_by=treasurer,
        updated_by=treasurer,
    )

    client = auth_client(member)
    response = client.post(
        "/api/v1/finance/contributions/record",
        {
            "chama_id": str(chama.id),
            "member_id": str(member.id),
            "contribution_type_id": str(contribution_type.id),
            "amount": "1200.00",
            "date_paid": str(date.today()),
            "method": "mpesa",
            "receipt_code": "RCP-004",
            "idempotency_key": "contrib-forbidden",
        },
        format="json",
    )

    assert response.status_code == 403
    assert (
        LedgerEntry.objects.filter(
            chama=chama, idempotency_key="contrib-forbidden"
        ).count()
        == 0
    )


def test_admin_cannot_approve_loan_without_treasurer_review():
    chama, admin, _, member = create_chama_with_memberships()
    member_client = auth_client(member)
    admin_client = auth_client(admin)

    request_resp = member_client.post(
        "/api/v1/finance/loans/request",
        {
            "chama_id": str(chama.id),
            "principal": "6000.00",
            "duration_months": 6,
        },
        format="json",
    )
    assert request_resp.status_code == 201
    loan_id = request_resp.json()["id"]

    approve_resp = admin_client.post(
        f"/api/v1/finance/loans/{loan_id}/approve",
        {"note": "Attempt approve without review"},
        format="json",
    )
    assert approve_resp.status_code == 400
    assert "treasurer review" in approve_resp.json()["detail"].lower()


def test_loan_eligibility_endpoint_returns_not_eligible_reason():
    chama, _, _, member = create_chama_with_memberships()
    policy = LoanProduct.objects.get(chama=chama, is_default=True)
    policy.max_loan_amount = "2000.00"
    policy.save(update_fields=["max_loan_amount", "updated_at"])

    member_client = auth_client(member)
    response = member_client.post(
        "/api/v1/finance/loans/eligibility",
        {
            "chama_id": str(chama.id),
            "principal": "12000.00",
            "duration_months": 6,
        },
        format="json",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["eligible"] is False
    assert "exceeds recommended maximum" in " ".join(body["reasons"]).lower()


def test_ledger_reverse_creates_counter_entry():
    chama, admin, treasurer, member = create_chama_with_memberships()
    contribution_type = ContributionType.objects.create(
        chama=chama,
        name="Monthly Reverse",
        frequency="monthly",
        default_amount="1000.00",
        created_by=treasurer,
        updated_by=treasurer,
    )
    treasurer_client = auth_client(treasurer)
    admin_client = auth_client(admin)

    contribution_resp = treasurer_client.post(
        "/api/v1/finance/contributions/record",
        {
            "chama_id": str(chama.id),
            "member_id": str(member.id),
            "contribution_type_id": str(contribution_type.id),
            "amount": "1200.00",
            "date_paid": str(date.today()),
            "method": "mpesa",
            "receipt_code": "RCP-REV-001",
            "idempotency_key": "contrib-reverse-1",
        },
        format="json",
    )
    assert contribution_resp.status_code == 201
    original_entry_id = contribution_resp.json()["ledger_entry"]["id"]

    reverse_resp = admin_client.post(
        f"/api/v1/finance/ledger/{original_entry_id}/reverse",
        {
            "idempotency_key": "reverse-entry-1",
            "reason": "Duplicate posting",
        },
        format="json",
    )
    assert reverse_resp.status_code == 201
    payload = reverse_resp.json()
    assert payload["reversal_entry"]["direction"] == "debit"
    assert payload["reversal_entry"]["reversal_of"] == original_entry_id
