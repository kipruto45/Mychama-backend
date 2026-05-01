from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.payments.models import (
    MpesaB2CPayout,
    MpesaB2CStatus,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentIntentType,
    PaymentPurpose,
)
from apps.payments.services import PaymentWorkflowService
from apps.payments.tasks import payments_retry_failed_b2c_payouts
from apps.payments.unified_models import (
    MpesaPaymentDetails,
    PaymentMethod,
    PaymentIntent as UnifiedPaymentIntent,
    PaymentReceipt,
    PaymentReceiptDownloadToken,
    PaymentStatus,
    PaymentTransaction,
    TransactionStatus,
)


class PaymentAutomationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            phone="+254744100001",
            password="password123",
            full_name="Payment Admin",
        )
        self.treasurer = user_model.objects.create_user(
            phone="+254744100002",
            password="password123",
            full_name="Payment Treasurer",
        )
        self.member = user_model.objects.create_user(
            phone="+254744100003",
            password="password123",
            full_name="Payment Member",
        )
        self.chama = Chama.objects.create(name="Payments Automation Chama")
        for user, role in [
            (self.admin, MembershipRole.CHAMA_ADMIN),
            (self.treasurer, MembershipRole.TREASURER),
            (self.member, MembershipRole.MEMBER),
        ]:
            Membership.objects.create(
                user=user,
                chama=self.chama,
                role=role,
                status=MemberStatus.ACTIVE,
                is_active=True,
                is_approved=True,
                joined_at=timezone.now(),
            )

    @patch("apps.notifications.services.NotificationService.send_notification")
    def test_retry_failed_b2c_alerts_after_retry_limit(self, send_notification_mock):
        intent = PaymentIntent.objects.create(
            chama=self.chama,
            user=self.member,
            amount=Decimal("1500.00"),
            purpose=PaymentPurpose.OTHER,
            intent_type=PaymentIntentType.WITHDRAWAL,
            reference_type="OTHER",
            reference_id=uuid.uuid4(),
            phone="+254744100003",
            status=PaymentIntentStatus.PENDING,
            idempotency_key="payout-retry-limit-1",
            created_by=self.admin,
            updated_by=self.admin,
        )
        for index in range(3):
            MpesaB2CPayout.objects.create(
                chama=self.chama,
                intent=intent,
                phone="+254744100003",
                amount=Decimal("1500.00"),
                originator_conversation_id=f"OC-FAIL-{index}",
                status=MpesaB2CStatus.FAILED,
                created_by=self.admin,
                updated_by=self.admin,
            )

        result = payments_retry_failed_b2c_payouts()
        payload = result.get("result", result)

        self.assertEqual(payload["retried"], 0)
        self.assertEqual(payload["escalated"], 1)
        self.assertGreaterEqual(payload["final_failure_alerts"], 1)
        self.assertGreaterEqual(send_notification_mock.call_count, 1)

    @patch("apps.notifications.services.NotificationService.publish_event")
    @patch("apps.notifications.services.NotificationService.send_notification")
    @patch.object(PaymentWorkflowService, "_post_intent_success")
    def test_b2c_success_notifies_leadership_and_group(
        self,
        post_success_mock,
        send_notification_mock,
        publish_event_mock,
    ):
        intent = PaymentIntent.objects.create(
            chama=self.chama,
            user=self.member,
            amount=Decimal("2100.00"),
            purpose=PaymentPurpose.OTHER,
            intent_type=PaymentIntentType.WITHDRAWAL,
            reference_type="OTHER",
            reference_id=uuid.uuid4(),
            phone="+254744100003",
            status=PaymentIntentStatus.PENDING,
            idempotency_key="payout-success-1",
            metadata={"member_id": str(self.member.id), "sent_by": str(self.admin.id)},
            created_by=self.admin,
            updated_by=self.admin,
        )
        payout = MpesaB2CPayout.objects.create(
            chama=self.chama,
            intent=intent,
            phone="+254744100003",
            amount=Decimal("2100.00"),
            originator_conversation_id="OC-SUCCESS-1",
            status=MpesaB2CStatus.PENDING,
            created_by=self.admin,
            updated_by=self.admin,
        )

        PaymentWorkflowService.process_b2c_result(
            {
                "Result": {
                    "OriginatorConversationID": payout.originator_conversation_id,
                    "ConversationID": "CONV-1",
                    "ResultCode": 0,
                    "ResultDesc": "Accepted",
                    "ResultParameters": {
                        "ResultParameter": [
                            {"Key": "TransactionID", "Value": "TX123"},
                            {"Key": "TransactionReceipt", "Value": "RCP123"},
                        ]
                    },
                }
            },
            source_ip=None,
            headers={},
        )

        payout.refresh_from_db()
        self.assertEqual(payout.status, MpesaB2CStatus.SUCCESS)
        self.assertGreaterEqual(send_notification_mock.call_count, 1)
        publish_event_mock.assert_called_once()
        post_success_mock.assert_called_once()


class PaymentReceiptPdfTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.client = APIClient()
        self.member = user_model.objects.create_user(
            phone="+254744200001",
            password="password123",
            full_name="Receipt Member",
        )
        self.chama = Chama.objects.create(name="Receipt PDF Chama")
        Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now(),
        )

        self.intent = PaymentIntent.objects.create(
            chama=self.chama,
            user=self.member,
            amount=Decimal("1200.00"),
            currency="KES",
            purpose=PaymentPurpose.OTHER,
            payment_method=PaymentMethod.MPESA,
            provider="safaricom",
            provider_intent_id="provider-intent-receipt-1",
            status=PaymentStatus.SUCCESS,
            idempotency_key=f"receipt-intent-{uuid.uuid4().hex[:20]}",
            completed_at=timezone.now(),
            created_by=self.member,
            updated_by=self.member,
        )
        self.transaction = PaymentTransaction.objects.create(
            payment_intent=self.intent,
            provider="mpesa",
            reference=f"TXN-RECEIPT-{uuid.uuid4().hex[:12].upper()}",
            provider_reference=f"MPESA-RECEIPT-{uuid.uuid4().hex[:12].upper()}",
            provider_name="safaricom",
            payment_method=PaymentMethod.MPESA,
            amount=Decimal("1200.00"),
            currency="KES",
            status=TransactionStatus.VERIFIED,
            payer_reference=self.member.phone,
            verified_by=self.member,
            verified_at=timezone.now(),
            created_by=self.member,
            updated_by=self.member,
        )
        self.receipt = PaymentReceipt.objects.create(
            payment_intent=self.intent,
            transaction=self.transaction,
            receipt_number=f"RCP-TEST-{uuid.uuid4().hex[:10].upper()}",
            reference_number=f"REF-TEST-{uuid.uuid4().hex[:10].upper()}",
            amount=Decimal("1200.00"),
            currency="KES",
            payment_method=PaymentMethod.MPESA,
            issued_by=self.member,
            created_by=self.member,
            updated_by=self.member,
        )

    def test_create_pdf_link_and_download_consumes_token(self):
        from urllib.parse import urlparse

        self.client.force_authenticate(self.member)
        response = self.client.post(
            f"/api/v1/payments/{self.intent.id}/receipt/pdf-link/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        download_url = response.json()["data"]["download_url"]
        download_path = urlparse(download_url).path

        # Public download does not require Authorization headers.
        self.client.force_authenticate(user=None)
        pdf_response = self.client.get(download_path)
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertTrue(pdf_response.content.startswith(b"%PDF"))

        reused_response = self.client.get(download_path)
        self.assertEqual(reused_response.status_code, 404)

    def test_expired_token_returns_not_found(self):
        token_record = PaymentReceiptDownloadToken.objects.create(
            payment_intent=self.intent,
            requested_by=self.member,
            expires_at=timezone.now() - timedelta(minutes=1),
            user_agent="tests",
            created_by=self.member,
            updated_by=self.member,
        )

        response = self.client.get(f"/api/v1/payments/receipt/pdf/{token_record.token}/")
        self.assertEqual(response.status_code, 404)


class UnifiedMpesaWebhookTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.member = user_model.objects.create_user(
            phone="+254744200001",
            password="password123",
            full_name="Webhook Member",
        )
        self.chama = Chama.objects.create(name="Unified Webhook Chama")
        Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            joined_at=timezone.now(),
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.member)

    def test_mpesa_webhook_success_finalizes_intent_and_sets_receipt_number(self):
        create_response = self.client.post(
            "/api/v1/payments/intents/",
            {
                "chama_id": str(self.chama.id),
                "amount": "250.00",
                "currency": "KES",
                "payment_method": "mpesa",
                "purpose": "contribution",
                "description": "Test contribution payment",
                "phone": "254744200001",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        intent_id = str(create_response.data["data"]["id"])
        provider_intent_id = str(create_response.data["data"]["provider_intent_id"])

        callback_payload = {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": "29115-34620561-1",
                    "CheckoutRequestID": provider_intent_id,
                    "ResultCode": 0,
                    "ResultDesc": "The service request is processed successfully.",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": 250},
                            {"Name": "MpesaReceiptNumber", "Value": "QHJ123ABC9"},
                            {"Name": "TransactionDate", "Value": 20260101010101},
                            {"Name": "PhoneNumber", "Value": 254744200001},
                        ]
                    },
                }
            }
        }

        webhook_response = self.client.post(
            "/api/v1/payments/webhook/?payment_method=mpesa&provider=safaricom",
            callback_payload,
            format="json",
        )
        self.assertEqual(webhook_response.status_code, 200)

        intent = UnifiedPaymentIntent.objects.get(id=intent_id)
        self.assertEqual(intent.status, PaymentStatus.SUCCESS)
        self.assertTrue(PaymentReceipt.objects.filter(payment_intent=intent).exists())
        mpesa_details = MpesaPaymentDetails.objects.get(payment_intent=intent)
        self.assertEqual(mpesa_details.mpesa_receipt_number, "QHJ123ABC9")
