from datetime import date

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MembershipRole
from apps.finance.models import ContributionType, LoanProduct
from apps.finance.services import FinanceService

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
    admin = create_user("+254703000001", "Reports Admin")
    treasurer = create_user("+254703000002", "Reports Treasurer")
    member = create_user("+254703000003", "Reports Member")
    other_member = create_user("+254703000004", "Reports Other Member")

    chama = Chama.objects.create(
        name="Reports Test Chama",
        description="Reports module tests",
        county="Nairobi",
        subcounty="Westlands",
        created_by=admin,
        updated_by=admin,
    )

    for user, role in [
        (admin, MembershipRole.CHAMA_ADMIN),
        (treasurer, MembershipRole.TREASURER),
        (member, MembershipRole.MEMBER),
        (other_member, MembershipRole.MEMBER),
    ]:
        Membership.objects.create(
            user=user,
            chama=chama,
            role=role,
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
        require_treasurer_review=False,
        require_separate_disburser=False,
        created_by=admin,
        updated_by=admin,
    )

    return chama, admin, treasurer, member, other_member


def test_member_statement_correctness_from_ledger():
    chama, admin, treasurer, member, _ = create_chama_with_memberships()
    contribution_type = ContributionType.objects.create(
        chama=chama,
        name="Monthly Savings",
        frequency="monthly",
        default_amount="1000.00",
        created_by=treasurer,
        updated_by=treasurer,
    )

    FinanceService.post_contribution(
        {
            "chama_id": str(chama.id),
            "member_id": str(member.id),
            "contribution_type_id": str(contribution_type.id),
            "amount": "1000.00",
            "date_paid": str(date.today()),
            "method": "mpesa",
            "receipt_code": "RPT-CNTR-001",
            "idempotency_key": "rpt-contribution-001",
        },
        treasurer,
    )

    loan = FinanceService.request_loan(
        {
            "chama_id": str(chama.id),
            "member_id": str(member.id),
            "principal": "5000.00",
            "interest_type": "flat",
            "interest_rate": "12.00",
            "duration_months": 6,
        },
        member,
    )
    FinanceService.approve_loan(loan.id, admin)
    FinanceService.disburse_loan(loan.id, admin)

    FinanceService.post_repayment(
        loan.id,
        {
            "amount": "1200.00",
            "date_paid": str(date.today()),
            "method": "mpesa",
            "receipt_code": "RPT-RPY-001",
            "idempotency_key": "rpt-repayment-001",
        },
        treasurer,
    )

    FinanceService.issue_penalty(
        {
            "chama_id": str(chama.id),
            "member_id": str(member.id),
            "amount": "200.00",
            "reason": "Late contribution",
            "due_date": str(date.today()),
            "idempotency_key": "rpt-penalty-001",
        },
        treasurer,
    )

    client = auth_client(admin)
    response = client.get(
        f"/api/v1/reports/member-statement?chama_id={chama.id}&member_id={member.id}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["contributions"] == "1000.00"
    assert payload["totals"]["loan_disbursements"] == "5000.00"
    assert payload["totals"]["repayments"] == "1200.00"
    assert payload["totals"]["penalties_debited"] == "200.00"
    assert payload["totals"]["closing_balance"] == "-3000.00"
    assert len(payload["ledger"]) >= 4


def test_member_cannot_fetch_other_members_statement():
    chama, _, _, member, other_member = create_chama_with_memberships()
    member_client = auth_client(member)

    forbidden = member_client.get(
        f"/api/v1/reports/member-statement?chama_id={chama.id}&member_id={other_member.id}"
    )
    assert forbidden.status_code == 403

    own = member_client.get(
        f"/api/v1/reports/member-statement?chama_id={chama.id}&member_id={member.id}"
    )
    assert own.status_code == 200


def test_loan_schedule_report_endpoint_returns_schedule():
    chama, admin, _, member, _ = create_chama_with_memberships()
    loan = FinanceService.request_loan(
        {
            "chama_id": str(chama.id),
            "member_id": str(member.id),
            "principal": "9000.00",
            "duration_months": 6,
        },
        member,
    )
    FinanceService.approve_loan(loan.id, admin)

    response = auth_client(admin).get(
        f"/api/v1/reports/loan-schedule?chama_id={chama.id}&loan_id={loan.id}"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["report_type"] == "loan_schedule"
    assert len(payload["schedule"]) == 6


def test_chama_health_and_forecast_endpoints():
    chama, admin, treasurer, member, _ = create_chama_with_memberships()
    contribution_type = ContributionType.objects.create(
        chama=chama,
        name="Monthly Savings",
        frequency="monthly",
        default_amount="1000.00",
        created_by=treasurer,
        updated_by=treasurer,
    )
    FinanceService.post_contribution(
        {
            "chama_id": str(chama.id),
            "member_id": str(member.id),
            "contribution_type_id": str(contribution_type.id),
            "amount": "1000.00",
            "date_paid": str(date.today()),
            "method": "mpesa",
            "receipt_code": "RPT-HLT-001",
            "idempotency_key": "rpt-hlt-001",
        },
        treasurer,
    )

    client = auth_client(admin)
    health = client.get(f"/api/v1/reports/chama-health?chama_id={chama.id}")
    forecast = client.get(
        f"/api/v1/reports/collection-forecast?chama_id={chama.id}&months=2"
    )
    risk = client.get(f"/api/v1/reports/defaulter-risk?chama_id={chama.id}")

    assert health.status_code == 200
    assert forecast.status_code == 200
    assert risk.status_code == 200
    assert "health_score" in health.json()
    assert len(forecast.json()["forecast"]) == 2
    assert risk.json()["report_type"] == "defaulter_risk"


def test_cohort_analysis_endpoint_returns_matrix():
    chama, admin, treasurer, member, _ = create_chama_with_memberships()
    contribution_type = ContributionType.objects.create(
        chama=chama,
        name="Cohort Savings",
        frequency="monthly",
        default_amount="500.00",
        created_by=treasurer,
        updated_by=treasurer,
    )
    FinanceService.post_contribution(
        {
            "chama_id": str(chama.id),
            "member_id": str(member.id),
            "contribution_type_id": str(contribution_type.id),
            "amount": "500.00",
            "date_paid": str(date.today()),
            "method": "mpesa",
            "receipt_code": "COHORT-001",
            "idempotency_key": "cohort-001",
        },
        treasurer,
    )

    admin_response = auth_client(admin).get(
        f"/api/v1/reports/cohort-analysis?chama_id={chama.id}&months=3"
    )
    assert admin_response.status_code == 200
    payload = admin_response.json()
    assert payload["report_type"] == "cohort_analysis"
    assert payload["horizon_months"] == 3
    assert payload["cohort_count"] >= 1

    member_response = auth_client(member).get(
        f"/api/v1/reports/cohort-analysis?chama_id={chama.id}&months=3"
    )
    assert member_response.status_code == 403
