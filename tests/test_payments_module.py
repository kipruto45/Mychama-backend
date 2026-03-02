from datetime import date
from decimal import Decimal

import pytest
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MembershipRole
from apps.finance.models import (
    Contribution,
    ContributionType,
    InstallmentSchedule,
    InstallmentStatus,
    LedgerDirection,
    LedgerEntry,
    Loan,
    LoanProduct,
    LoanStatus,
    Repayment,
)
from apps.finance.services import FinanceService
from apps.payments.models import (
    MpesaC2BTransaction,
    PaymentDispute,
    PaymentDisputeStatus,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentIntentType,
    PaymentRefund,
    PaymentRefundStatus,
)
from apps.payments.services import PaymentWorkflowService
from apps.payments.views_frontend import loan_pay_view

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


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


def create_base_setup():
    admin = create_user("+254711000001", "Admin User")
    treasurer = create_user("+254711000002", "Treasurer User")
    member = create_user("+254711000003", "Member User")
    member_two = create_user("+254711000004", "Second Member")
    auditor = create_user("+254711000005", "Auditor User")

    chama = Chama.objects.create(
        name="Payments Workflow Chama",
        description="Payments module test chama",
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
    Membership.objects.create(
        user=member_two,
        chama=chama,
        role=MembershipRole.MEMBER,
        is_active=True,
        is_approved=True,
        approved_by=admin,
        created_by=admin,
        updated_by=admin,
    )
    Membership.objects.create(
        user=auditor,
        chama=chama,
        role=MembershipRole.AUDITOR,
        is_active=True,
        is_approved=True,
        approved_by=admin,
        created_by=admin,
        updated_by=admin,
    )

    contribution_type = ContributionType.objects.create(
        chama=chama,
        name="Monthly Contribution",
        frequency="monthly",
        default_amount="1000.00",
        is_active=True,
        created_by=admin,
        updated_by=admin,
    )

    loan_product = LoanProduct.objects.create(
        chama=chama,
        name="Standard Loan",
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

    return {
        "chama": chama,
        "admin": admin,
        "treasurer": treasurer,
        "member": member,
        "member_two": member_two,
        "auditor": auditor,
        "contribution_type": contribution_type,
        "loan_product": loan_product,
    }


def seed_chama_balance(*, chama, member, treasurer, contribution_type):
    FinanceService.post_contribution(
        {
            "chama_id": chama.id,
            "member_id": member.id,
            "contribution_type_id": contribution_type.id,
            "amount": Decimal("30000.00"),
            "date_paid": date.today(),
            "method": "mpesa",
            "receipt_code": f"SEED-{timezone.now().timestamp()}",
            "idempotency_key": f"seed-balance:{chama.id}:{timezone.now().timestamp()}",
        },
        treasurer,
    )


def request_and_approve_loan(*, setup):
    member_client = auth_client(setup["member"])
    treasurer_client = auth_client(setup["treasurer"])
    admin_client = auth_client(setup["admin"])

    request_resp = member_client.post(
        "/api/v1/finance/loans/request",
        {
            "chama_id": str(setup["chama"].id),
            "principal": "12000.00",
            "duration_months": 6,
            "loan_product_id": str(setup["loan_product"].id),
        },
        format="json",
    )
    assert request_resp.status_code == 201
    loan_id = request_resp.json()["id"]

    review_resp = treasurer_client.post(
        f"/api/v1/finance/loans/{loan_id}/review",
        {"decision": "approved", "note": "Treasurer approved"},
        format="json",
    )
    assert review_resp.status_code == 200

    approve_resp = admin_client.post(
        f"/api/v1/finance/loans/{loan_id}/approve",
        {"note": "Admin approved"},
        format="json",
    )
    assert approve_resp.status_code == 200

    return Loan.objects.get(id=loan_id)


# ---------------------------------------------------------------------------
# Core payments tests
# ---------------------------------------------------------------------------


def test_member_cannot_initiate_deposit_if_membership_not_approved(settings):
    settings.MPESA_USE_STUB = True
    setup = create_base_setup()

    blocked_member = create_user("+254711000099", "Blocked Member")
    Membership.objects.create(
        user=blocked_member,
        chama=setup["chama"],
        role=MembershipRole.MEMBER,
        is_active=True,
        is_approved=False,
        created_by=setup["admin"],
        updated_by=setup["admin"],
    )

    response = auth_client(blocked_member).post(
        "/api/v1/payments/deposit/stk/initiate",
        {
            "chama_id": str(setup["chama"].id),
            "amount": "1500.00",
            "purpose": "CONTRIBUTION",
            "reference_id": str(setup["contribution_type"].id),
        },
        format="json",
    )

    assert response.status_code == 400
    assert "approved active" in response.json()["detail"].lower()


def test_c2b_confirmation_duplicate_trans_id_does_not_double_post_ledger(settings):
    settings.MPESA_USE_STUB = True
    setup = create_base_setup()

    member_client = auth_client(setup["member"])
    intent_resp = member_client.post(
        "/api/v1/payments/deposit/c2b/intent",
        {
            "chama_id": str(setup["chama"].id),
            "amount": "2000.00",
            "purpose": "CONTRIBUTION",
            "reference_id": str(setup["contribution_type"].id),
            "idempotency_key": "deposit-c2b-001",
        },
        format="json",
    )
    assert intent_resp.status_code == 201
    account_ref = intent_resp.json()["instructions"]["account_reference"]

    payload = {
        "TransID": "C2B-TX-001",
        "TransAmount": "2000.00",
        "BillRefNumber": account_ref,
        "MSISDN": setup["member"].phone,
        "TransTime": timezone.now().strftime("%Y%m%d%H%M%S"),
    }

    callback_client = APIClient()
    first = callback_client.post(
        "/api/v1/payments/callbacks/c2b/confirmation",
        payload,
        format="json",
    )
    second = callback_client.post(
        "/api/v1/payments/callbacks/c2b/confirmation",
        payload,
        format="json",
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert Contribution.objects.filter(receipt_code="C2B-TX-001").count() == 1
    assert MpesaC2BTransaction.objects.filter(trans_id="C2B-TX-001").count() == 1


def test_stk_callback_duplicate_does_not_double_post_ledger(settings):
    settings.MPESA_USE_STUB = True
    setup = create_base_setup()

    response = auth_client(setup["member"]).post(
        "/api/v1/payments/deposit/stk/initiate",
        {
            "chama_id": str(setup["chama"].id),
            "amount": "1800.00",
            "purpose": "CONTRIBUTION",
            "reference_id": str(setup["contribution_type"].id),
            "idempotency_key": "deposit-stk-dup-001",
        },
        format="json",
    )
    assert response.status_code == 201
    checkout_id = response.json()["checkout_request_id"]

    payload = {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "MR-TEST-001",
                "CheckoutRequestID": checkout_id,
                "ResultCode": 0,
                "ResultDesc": "Processed",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": 1800.0},
                        {"Name": "MpesaReceiptNumber", "Value": "STK-RCP-001"},
                        {"Name": "PhoneNumber", "Value": 254711000003},
                    ]
                },
            }
        }
    }

    callback_client = APIClient()
    first = callback_client.post(
        "/api/v1/payments/callbacks/stk", payload, format="json"
    )
    second = callback_client.post(
        "/api/v1/payments/callbacks/stk", payload, format="json"
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert Contribution.objects.filter(receipt_code="STK-RCP-001").count() == 1


def test_b2c_result_callback_posts_ledger_once(settings):
    settings.MPESA_USE_STUB = True
    setup = create_base_setup()
    seed_chama_balance(
        chama=setup["chama"],
        member=setup["member"],
        treasurer=setup["treasurer"],
        contribution_type=setup["contribution_type"],
    )

    treasurer_client = auth_client(setup["treasurer"])
    admin_client = auth_client(setup["admin"])

    request_resp = treasurer_client.post(
        "/api/v1/payments/withdraw/request",
        {
            "chama_id": str(setup["chama"].id),
            "amount": "1200.00",
            "phone": setup["member"].phone,
            "purpose": "OTHER",
            "reason": "Member withdrawal",
            "idempotency_key": "withdraw-001",
        },
        format="json",
    )
    assert request_resp.status_code == 201
    intent_id = request_resp.json()["id"]

    assert (
        treasurer_client.post(
            f"/api/v1/payments/withdraw/{intent_id}/approve",
            {"note": "Treasurer check"},
            format="json",
        ).status_code
        == 200
    )
    assert (
        admin_client.post(
            f"/api/v1/payments/withdraw/{intent_id}/approve",
            {"note": "Admin approval"},
            format="json",
        ).status_code
        == 200
    )

    send_resp = admin_client.post(
        f"/api/v1/payments/withdraw/{intent_id}/send",
        format="json",
    )
    assert send_resp.status_code == 200
    originator_id = send_resp.json()["originator_conversation_id"]

    result_payload = {
        "Result": {
            "ResultCode": 0,
            "ResultDesc": "Accepted",
            "OriginatorConversationID": originator_id,
            "ConversationID": "AG-12345",
            "ResultParameters": {
                "ResultParameter": [
                    {"Key": "TransactionID", "Value": "B2C-TX-001"},
                ]
            },
        }
    }

    callback_client = APIClient()
    first = callback_client.post(
        "/api/v1/payments/callbacks/b2c/result",
        result_payload,
        format="json",
    )
    second = callback_client.post(
        "/api/v1/payments/callbacks/b2c/result",
        result_payload,
        format="json",
    )

    assert first.status_code == 200
    assert second.status_code == 200

    intent = PaymentIntent.objects.get(id=intent_id)
    assert intent.status == PaymentIntentStatus.SUCCESS
    assert (
        LedgerEntry.objects.filter(
            chama=setup["chama"],
            direction=LedgerDirection.DEBIT,
            reference_type="ManualAdjustment",
        ).count()
        == 1
    )


def test_reconciliation_flags_missing_ledger_entry():
    setup = create_base_setup()

    PaymentIntent.objects.create(
        chama=setup["chama"],
        intent_type=PaymentIntentType.DEPOSIT,
        purpose="CONTRIBUTION",
        reference_type="CONTRIBUTION_TYPE",
        reference_id=setup["contribution_type"].id,
        amount="500.00",
        phone=setup["member"].phone,
        status=PaymentIntentStatus.SUCCESS,
        idempotency_key="recon-missing-ledger-001",
        metadata={"member_id": str(setup["member"].id), "external_reference": "X001"},
        created_by=setup["member"],
        updated_by=setup["member"],
    )

    run = PaymentWorkflowService.run_reconciliation(
        chama_id=setup["chama"].id,
        actor=setup["admin"],
    )

    anomalies = run.anomalies.get("missing_ledger_for_success_intents", [])
    assert len(anomalies) >= 1


def test_stk_callback_rejects_non_allowlisted_ip_when_strict(settings):
    settings.PAYMENTS_CALLBACK_REQUIRE_IP_ALLOWLIST = True
    settings.MPESA_CALLBACK_IP_ALLOWLIST = ["196.201.214.200"]

    payload = {
        "Body": {
            "stkCallback": {
                "CheckoutRequestID": "ws_CO_TEST_001",
                "ResultCode": 0,
                "ResultDesc": "Processed",
            }
        }
    }
    response = APIClient().post(
        "/api/v1/payments/callbacks/stk",
        payload,
        format="json",
        REMOTE_ADDR="10.10.10.10",
    )
    assert response.status_code == 403
    assert "forbidden callback source" in response.json()["detail"].lower()


def test_stk_callback_rejects_invalid_signature_when_required(settings):
    settings.PAYMENTS_CALLBACK_REQUIRE_SIGNATURE = True
    settings.MPESA_CALLBACK_SECRET = "callback-secret"

    payload = {
        "Body": {
            "stkCallback": {
                "CheckoutRequestID": "ws_CO_TEST_002",
                "ResultCode": 0,
                "ResultDesc": "Processed",
            }
        }
    }
    response = APIClient().post(
        "/api/v1/payments/callbacks/stk",
        payload,
        format="json",
        HTTP_X_MPESA_SIGNATURE="invalid-signature",
    )
    assert response.status_code == 403
    assert "forbidden callback source" in response.json()["detail"].lower()


def test_legacy_stk_callback_rejects_invalid_signature_when_required(settings):
    settings.PAYMENTS_CALLBACK_REQUIRE_SIGNATURE = True
    settings.MPESA_CALLBACK_SECRET = "callback-secret"

    payload = {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "MR-LEGACY-001",
                "CheckoutRequestID": "ws_CO_LEGACY_001",
                "ResultCode": 0,
                "ResultDesc": "Processed",
            }
        }
    }
    response = APIClient().post(
        "/api/v1/payments/mpesa/callback",
        payload,
        format="json",
        HTTP_X_MPESA_SIGNATURE="invalid-signature",
    )
    assert response.status_code == 403
    assert "forbidden callback source" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Loan disbursement + repayment workflow tests
# ---------------------------------------------------------------------------


def test_loan_approved_creates_loan_disbursement_payment_intent(settings):
    settings.MPESA_USE_STUB = True
    setup = create_base_setup()

    loan = request_and_approve_loan(setup=setup)

    intent = PaymentIntent.objects.filter(
        chama=setup["chama"],
        intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
        reference_type="LOAN",
        reference_id=loan.id,
    ).first()
    assert intent is not None
    assert intent.status in {PaymentIntentStatus.INITIATED, PaymentIntentStatus.PENDING}


def test_only_treasurer_admin_can_approve_or_send_disbursement(settings):
    settings.MPESA_USE_STUB = True
    setup = create_base_setup()
    seed_chama_balance(
        chama=setup["chama"],
        member=setup["member"],
        treasurer=setup["treasurer"],
        contribution_type=setup["contribution_type"],
    )

    loan = request_and_approve_loan(setup=setup)
    intent = PaymentIntent.objects.get(
        chama=setup["chama"],
        intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
        reference_id=loan.id,
    )

    member_client = auth_client(setup["member"])
    auditor_client = auth_client(setup["auditor"])
    treasurer_client = auth_client(setup["treasurer"])
    admin_client = auth_client(setup["admin"])

    assert (
        member_client.post(
            f"/api/v1/payments/loan-disbursements/{intent.id}/approve",
            {},
            format="json",
        ).status_code
        == 400
    )
    assert (
        auditor_client.post(
            f"/api/v1/payments/loan-disbursements/{intent.id}/send",
            {},
            format="json",
        ).status_code
        == 400
    )

    assert (
        treasurer_client.post(
            f"/api/v1/payments/loan-disbursements/{intent.id}/approve",
            {"note": "Treasurer approved"},
            format="json",
        ).status_code
        == 200
    )
    assert (
        admin_client.post(
            f"/api/v1/payments/loan-disbursements/{intent.id}/approve",
            {"note": "Admin approved"},
            format="json",
        ).status_code
        == 200
    )


def test_b2c_loan_disbursement_success_updates_loan_status_and_posts_once(settings):
    settings.MPESA_USE_STUB = True
    setup = create_base_setup()
    seed_chama_balance(
        chama=setup["chama"],
        member=setup["member"],
        treasurer=setup["treasurer"],
        contribution_type=setup["contribution_type"],
    )

    loan = request_and_approve_loan(setup=setup)
    intent = PaymentIntent.objects.get(
        chama=setup["chama"],
        intent_type=PaymentIntentType.LOAN_DISBURSEMENT,
        reference_id=loan.id,
    )

    treasurer_client = auth_client(setup["treasurer"])
    admin_client = auth_client(setup["admin"])

    assert (
        treasurer_client.post(
            f"/api/v1/payments/loan-disbursements/{intent.id}/approve",
            {"note": "Treasurer approved"},
            format="json",
        ).status_code
        == 200
    )
    assert (
        admin_client.post(
            f"/api/v1/payments/loan-disbursements/{intent.id}/approve",
            {"note": "Admin approved"},
            format="json",
        ).status_code
        == 200
    )

    send_resp = admin_client.post(
        f"/api/v1/payments/loan-disbursements/{intent.id}/send",
        format="json",
    )
    assert send_resp.status_code == 200
    originator_id = send_resp.json()["originator_conversation_id"]

    payload = {
        "Result": {
            "ResultCode": 0,
            "ResultDesc": "Accepted",
            "OriginatorConversationID": originator_id,
            "ConversationID": "AG-LOAN-1",
            "ResultParameters": {
                "ResultParameter": [
                    {"Key": "TransactionID", "Value": "B2C-LOAN-001"},
                ]
            },
        }
    }

    callback_client = APIClient()
    first = callback_client.post(
        "/api/v1/payments/callbacks/b2c/result",
        payload,
        format="json",
    )
    second = callback_client.post(
        "/api/v1/payments/callbacks/b2c/result",
        payload,
        format="json",
    )

    assert first.status_code == 200
    assert second.status_code == 200

    loan.refresh_from_db()
    assert loan.status in {LoanStatus.DISBURSED, LoanStatus.ACTIVE}
    assert (
        LedgerEntry.objects.filter(
            chama=setup["chama"],
            reference_type="Loan",
            reference_id=loan.id,
        ).count()
        == 1
    )


def test_loan_repayment_stk_success_posts_repayment_and_updates_schedule_once(settings):
    settings.MPESA_USE_STUB = True
    setup = create_base_setup()

    loan = Loan.objects.create(
        chama=setup["chama"],
        member=setup["member"],
        loan_product=setup["loan_product"],
        principal="6000.00",
        interest_type="flat",
        interest_rate="12.00",
        duration_months=6,
        status=LoanStatus.ACTIVE,
        created_by=setup["admin"],
        updated_by=setup["admin"],
    )
    InstallmentSchedule.objects.create(
        loan=loan,
        due_date=timezone.localdate(),
        expected_amount="1200.00",
        expected_principal="1000.00",
        expected_interest="200.00",
        expected_penalty="0.00",
        status=InstallmentStatus.DUE,
        created_by=setup["admin"],
        updated_by=setup["admin"],
    )

    member_client = auth_client(setup["member"])
    initiate_resp = member_client.post(
        f"/api/v1/payments/loans/{loan.id}/repay/stk/initiate",
        {"chama_id": str(setup["chama"].id), "amount": "1200.00"},
        format="json",
    )
    assert initiate_resp.status_code == 201
    checkout_id = initiate_resp.json()["checkout_request_id"]

    callback_payload = {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "MR-REPAY-001",
                "CheckoutRequestID": checkout_id,
                "ResultCode": 0,
                "ResultDesc": "Processed",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": 1200.0},
                        {"Name": "MpesaReceiptNumber", "Value": "RCP-REPAY-001"},
                        {"Name": "PhoneNumber", "Value": 254711000003},
                    ]
                },
            }
        }
    }

    callback_client = APIClient()
    callback_client.post(
        "/api/v1/payments/callbacks/stk", callback_payload, format="json"
    )
    callback_client.post(
        "/api/v1/payments/callbacks/stk", callback_payload, format="json"
    )

    assert (
        Repayment.objects.filter(loan=loan, receipt_code="RCP-REPAY-001").count() == 1
    )
    schedule = InstallmentSchedule.objects.get(loan=loan)
    assert schedule.status == InstallmentStatus.PAID


# ---------------------------------------------------------------------------
# Permissions / frontend access tests
# ---------------------------------------------------------------------------


def test_member_can_access_loan_pay_page_only_for_own_loan():
    setup = create_base_setup()

    loan_owned = Loan.objects.create(
        chama=setup["chama"],
        member=setup["member"],
        principal="5000.00",
        interest_type="flat",
        interest_rate="12.00",
        duration_months=6,
        status=LoanStatus.ACTIVE,
        created_by=setup["admin"],
        updated_by=setup["admin"],
    )
    loan_other = Loan.objects.create(
        chama=setup["chama"],
        member=setup["member_two"],
        principal="4000.00",
        interest_type="flat",
        interest_rate="12.00",
        duration_months=6,
        status=LoanStatus.ACTIVE,
        created_by=setup["admin"],
        updated_by=setup["admin"],
    )

    rf = RequestFactory()

    own_request = rf.get("/")
    own_request.user = setup["member"]
    own_response = loan_pay_view(own_request, loan_owned.id)
    assert own_response.status_code == 200

    other_request = rf.get("/")
    other_request.user = setup["member"]
    other_response = loan_pay_view(other_request, loan_other.id)
    assert other_response.status_code == 403


def test_member_cannot_view_admin_transactions_or_disbursement_queue(settings):
    settings.MPESA_USE_STUB = True
    setup = create_base_setup()

    member_client = auth_client(setup["member"])
    admin_tx = member_client.get(
        "/api/v1/payments/admin/transactions",
        {"chama_id": str(setup["chama"].id)},
    )
    disburse_queue = member_client.get(
        "/api/v1/payments/loan-disbursements/pending",
        {"chama_id": str(setup["chama"].id)},
    )

    assert admin_tx.status_code == 403
    assert disburse_queue.status_code == 403


def test_auditor_is_read_only_for_payments(settings):
    settings.MPESA_USE_STUB = True
    setup = create_base_setup()

    auditor_client = auth_client(setup["auditor"])

    read_resp = auditor_client.get(
        "/api/v1/payments/admin/transactions",
        {"chama_id": str(setup["chama"].id)},
    )
    write_resp = auditor_client.post(
        "/api/v1/payments/withdraw/request",
        {
            "chama_id": str(setup["chama"].id),
            "amount": "1000.00",
            "phone": setup["member"].phone,
            "purpose": "OTHER",
            "reason": "Auditor write attempt",
        },
        format="json",
    )

    assert read_resp.status_code == 200
    assert write_resp.status_code == 400


def test_split_stk_payment_posts_repayment_and_contribution_once(settings):
    settings.MPESA_USE_STUB = True
    setup = create_base_setup()

    loan = Loan.objects.create(
        chama=setup["chama"],
        member=setup["member"],
        loan_product=setup["loan_product"],
        principal="4000.00",
        interest_type="flat",
        interest_rate="12.00",
        duration_months=6,
        status=LoanStatus.ACTIVE,
        created_by=setup["admin"],
        updated_by=setup["admin"],
    )
    InstallmentSchedule.objects.create(
        loan=loan,
        due_date=timezone.localdate(),
        expected_amount="1000.00",
        expected_principal="800.00",
        expected_interest="200.00",
        expected_penalty="0.00",
        status=InstallmentStatus.DUE,
        created_by=setup["admin"],
        updated_by=setup["admin"],
    )

    response = auth_client(setup["member"]).post(
        "/api/v1/payments/split/stk/initiate",
        {
            "chama_id": str(setup["chama"].id),
            "loan_id": str(loan.id),
            "contribution_type_id": str(setup["contribution_type"].id),
            "amount": "1500.00",
            "strategy": "repayment_first",
            "idempotency_key": "split-stk-001",
        },
        format="json",
    )
    assert response.status_code == 201
    checkout_id = response.json()["checkout_request_id"]

    payload = {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "MR-SPLIT-001",
                "CheckoutRequestID": checkout_id,
                "ResultCode": 0,
                "ResultDesc": "Processed",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": 1500.0},
                        {"Name": "MpesaReceiptNumber", "Value": "SPLIT-RCP-001"},
                        {"Name": "PhoneNumber", "Value": 254711000003},
                    ]
                },
            }
        }
    }

    callback_client = APIClient()
    first = callback_client.post(
        "/api/v1/payments/callbacks/stk", payload, format="json"
    )
    second = callback_client.post(
        "/api/v1/payments/callbacks/stk", payload, format="json"
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert (
        Repayment.objects.filter(loan=loan, receipt_code="SPLIT-RCP-001-R").count() == 1
    )
    assert Contribution.objects.filter(receipt_code="SPLIT-RCP-001-C").count() == 1


def test_refund_workflow_creates_reversal_entry(settings):
    settings.MPESA_USE_STUB = True
    setup = create_base_setup()

    member_client = auth_client(setup["member"])
    admin_client = auth_client(setup["admin"])
    treasurer_client = auth_client(setup["treasurer"])

    intent_resp = member_client.post(
        "/api/v1/payments/deposit/c2b/intent",
        {
            "chama_id": str(setup["chama"].id),
            "amount": "2500.00",
            "purpose": "CONTRIBUTION",
            "reference_id": str(setup["contribution_type"].id),
            "idempotency_key": "deposit-c2b-refund-001",
        },
        format="json",
    )
    assert intent_resp.status_code == 201
    intent_id = intent_resp.json()["intent"]["id"]
    account_ref = intent_resp.json()["instructions"]["account_reference"]

    callback_payload = {
        "TransID": "C2B-REFUND-001",
        "TransAmount": "2500.00",
        "BillRefNumber": account_ref,
        "MSISDN": setup["member"].phone,
        "TransTime": timezone.now().strftime("%Y%m%d%H%M%S"),
    }
    callback_client = APIClient()
    callback_resp = callback_client.post(
        "/api/v1/payments/callbacks/c2b/confirmation",
        callback_payload,
        format="json",
    )
    assert callback_resp.status_code == 200

    request_refund_resp = member_client.post(
        "/api/v1/payments/refunds/request",
        {
            "chama_id": str(setup["chama"].id),
            "intent_id": intent_id,
            "amount": "2500.00",
            "reason": "Duplicate payment reported",
            "idempotency_key": "refund-001",
        },
        format="json",
    )
    assert request_refund_resp.status_code == 201
    refund_id = request_refund_resp.json()["id"]

    approve_resp = admin_client.post(
        f"/api/v1/payments/refunds/{refund_id}/approve",
        {"approve": True, "note": "Approved"},
        format="json",
    )
    assert approve_resp.status_code == 200

    process_resp = treasurer_client.post(
        f"/api/v1/payments/refunds/{refund_id}/process",
        format="json",
    )
    assert process_resp.status_code == 200

    refund = PaymentRefund.objects.get(id=refund_id)
    assert refund.status == PaymentRefundStatus.PROCESSED
    assert refund.ledger_reversal_entry_id is not None


def test_payment_dispute_open_and_resolve_flow(settings):
    settings.MPESA_USE_STUB = True
    setup = create_base_setup()

    member_client = auth_client(setup["member"])
    treasurer_client = auth_client(setup["treasurer"])

    intent_resp = member_client.post(
        "/api/v1/payments/deposit/c2b/intent",
        {
            "chama_id": str(setup["chama"].id),
            "amount": "1200.00",
            "purpose": "CONTRIBUTION",
            "reference_id": str(setup["contribution_type"].id),
            "idempotency_key": "deposit-c2b-dispute-001",
        },
        format="json",
    )
    assert intent_resp.status_code == 201
    intent_id = intent_resp.json()["intent"]["id"]

    dispute_resp = member_client.post(
        "/api/v1/payments/disputes",
        {
            "chama_id": str(setup["chama"].id),
            "intent_id": intent_id,
            "category": "incorrect_amount",
            "reason": "Amount mismatch on receipt",
        },
        format="json",
    )
    assert dispute_resp.status_code == 201
    dispute_id = dispute_resp.json()["id"]

    resolve_resp = treasurer_client.post(
        f"/api/v1/payments/disputes/{dispute_id}/resolve",
        {"status": "RESOLVED", "resolution_notes": "Verified and corrected"},
        format="json",
    )
    assert resolve_resp.status_code == 200

    dispute = PaymentDispute.objects.get(id=dispute_id)
    assert dispute.status == PaymentDisputeStatus.RESOLVED
