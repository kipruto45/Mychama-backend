from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import OTPPurpose, User, UserPreference
from apps.accounts.services import OTPDeliveryError, OTPService
from apps.billing.credits import issue_billing_credit
from apps.billing.metering import set_usage
from apps.billing.models import (
    BillingCredit,
    BillingCreditAllocation,
    Invoice,
    Plan,
    Subscription,
)
from apps.billing.security import encrypt_billing_metadata
from apps.billing.services import (
    cleanup_credit_reservations,
    create_checkout_invoice,
    get_access_status,
    process_payment_retries,
    send_credit_expiry_reminders,
    send_renewal_reminders,
)
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.finance.models import ContributionType
from apps.notifications.models import Notification
from apps.payments.models import MpesaTransaction


class BillingCheckoutFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone='+254700001100',
            password='testpass123',
            full_name='Billing Tester',
        )
        self.client.force_authenticate(user=self.user)

        self.chama = Chama.objects.create(name='Billing Checkout Test Chama')
        self.membership = Membership.objects.create(
            user=self.user,
            chama=self.chama,
            role=MembershipRole.ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            approved_by=self.user,
            approved_at=timezone.now(),
            created_by=self.user,
            updated_by=self.user,
        )
        self.contribution_type = ContributionType.objects.create(
            chama=self.chama,
            name='Monthly Contribution',
            default_amount='100.00',
        )
        self.pro_plan = Plan.objects.get(code=Plan.PRO)

    def test_manual_checkout_confirmation_activates_selected_plan(self):
        response = self.client.post(
            '/api/v1/billing/checkout/confirm/',
            {
                'plan_id': self.pro_plan.id,
                'billing_cycle': 'monthly',
                'provider': Subscription.MANUAL,
                'session_id': 'manual_session_001',
                'chama_id': str(self.chama.id),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        subscription = Subscription.objects.get(chama=self.chama)
        self.assertEqual(subscription.plan, self.pro_plan)
        self.assertEqual(subscription.status, Subscription.ACTIVE)
        self.assertEqual(subscription.provider, Subscription.MANUAL)
        self.assertEqual(subscription.provider_subscription_id, 'manual_session_001')
        self.assertTrue(get_access_status(self.chama)['is_paid'])
        self.assertEqual(Invoice.objects.filter(chama=self.chama, status=Invoice.PAID).count(), 1)

    def test_checkout_creates_pending_invoice_and_returns_proration_context(self):
        response = self.client.post(
            '/api/v1/billing/checkout/',
            {
                'plan_id': self.pro_plan.id,
                'billing_cycle': 'monthly',
                'provider': 'manual',
                'success_url': 'http://localhost:3000',
                'cancel_url': 'http://localhost:3000',
                'chama_id': str(self.chama.id),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('invoice', response.data)
        self.assertEqual(response.data['provider'], 'manual')
        self.assertEqual(Invoice.objects.filter(chama=self.chama, status=Invoice.PENDING).count(), 1)

    def test_checkout_reserves_referral_credit_and_discounts_invoice(self):
        credit = issue_billing_credit(
            chama=self.chama,
            amount='1000.00',
            source_type=BillingCredit.REFERRAL,
            source_reference='reward-test-001',
            description='Referral reward credit',
        )

        response = self.client.post(
            '/api/v1/billing/checkout/',
            {
                'plan_id': self.pro_plan.id,
                'billing_cycle': 'monthly',
                'provider': 'manual',
                'success_url': 'http://localhost:3000',
                'cancel_url': 'http://localhost:3000',
                'chama_id': str(self.chama.id),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['proration']['referral_credit_amount'], '1000.00')

        invoice = Invoice.objects.get(chama=self.chama, status=Invoice.PENDING)
        self.assertEqual(invoice.subtotal, self.pro_plan.monthly_price - Decimal('1000.00'))
        self.assertTrue(
            invoice.line_items.filter(metadata__type='referral_credit').exists()
        )

        credit.refresh_from_db()
        self.assertEqual(credit.remaining_amount, Decimal('0.00'))

    def test_checkout_auto_applies_when_referral_credit_covers_full_upgrade(self):
        credit = issue_billing_credit(
            chama=self.chama,
            amount='7000.00',
            source_type=BillingCredit.REFERRAL,
            source_reference='reward-test-002',
            description='Full upgrade credit',
        )

        response = self.client.post(
            '/api/v1/billing/checkout/',
            {
                'plan_id': self.pro_plan.id,
                'billing_cycle': 'monthly',
                'provider': 'manual',
                'success_url': 'http://localhost:3000',
                'cancel_url': 'http://localhost:3000',
                'chama_id': str(self.chama.id),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['auto_applied'])
        self.assertEqual(response.data['session_id'], None)

        invoice = Invoice.objects.get(chama=self.chama, status=Invoice.PAID)
        subscription = Subscription.objects.get(chama=self.chama)
        self.assertEqual(invoice.total_amount, Decimal('0.00'))
        self.assertEqual(subscription.plan, self.pro_plan)
        self.assertEqual(subscription.status, Subscription.ACTIVE)

        credit.refresh_from_db()
        self.assertEqual(
            credit.remaining_amount,
            Decimal('7000.00') - self.pro_plan.monthly_price,
        )

    def test_staff_can_issue_manual_credit_for_active_chama(self):
        staff_user = User.objects.create_user(
            phone='+254700001101',
            password='testpass123',
            full_name='Platform Staff',
            is_staff=True,
        )
        self.client.force_authenticate(user=staff_user)
        custom_expiry = (timezone.now() + timedelta(days=10)).isoformat()

        response = self.client.post(
            '/api/v1/billing/admin/credits/',
            {
                'chama_id': str(self.chama.id),
                'amount': '2500.00',
                'description': 'Support goodwill credit',
                'expires_at': custom_expiry,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        credit = BillingCredit.objects.get(chama=self.chama, source_type=BillingCredit.MANUAL)
        self.assertEqual(credit.total_amount, Decimal('2500.00'))
        self.assertEqual(credit.remaining_amount, Decimal('2500.00'))
        self.assertIsNotNone(credit.expires_at)
        self.assertEqual(credit.expires_at.isoformat(), custom_expiry)

    def test_staff_credit_admin_view_returns_picker_options(self):
        staff_user = User.objects.create_user(
            phone='+254700001102',
            password='testpass123',
            full_name='Platform Staff Two',
            is_staff=True,
        )
        self.client.force_authenticate(user=staff_user)

        response = self.client.get('/api/v1/billing/admin/credits/', {'q': 'Billing'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('available_chamas', response.data)
        self.assertTrue(
            any(item['id'] == str(self.chama.id) for item in response.data['available_chamas'])
        )

    def test_staff_can_update_existing_credit(self):
        staff_user = User.objects.create_user(
            phone='+254700001103',
            password='testpass123',
            full_name='Platform Staff Three',
            is_staff=True,
        )
        credit = issue_billing_credit(
            chama=self.chama,
            amount='1800.00',
            source_type=BillingCredit.MANUAL,
            description='Original credit',
        )
        self.client.force_authenticate(user=staff_user)

        response = self.client.patch(
            f'/api/v1/billing/admin/credits/{credit.id}/',
            {
                'remaining_amount': '900.00',
                'description': 'Adjusted credit',
                'expires_at': None,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        credit.refresh_from_db()
        self.assertEqual(credit.remaining_amount, Decimal('900.00'))
        self.assertEqual(credit.description, 'Adjusted credit')
        self.assertIsNone(credit.expires_at)

    def test_staff_can_revoke_existing_credit(self):
        staff_user = User.objects.create_user(
            phone='+254700001104',
            password='testpass123',
            full_name='Platform Staff Four',
            is_staff=True,
        )
        credit = issue_billing_credit(
            chama=self.chama,
            amount='1200.00',
            source_type=BillingCredit.MANUAL,
            description='Revoke test credit',
        )
        self.client.force_authenticate(user=staff_user)

        response = self.client.patch(
            f'/api/v1/billing/admin/credits/{credit.id}/',
            {'action': 'revoke'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        credit.refresh_from_db()
        self.assertEqual(credit.remaining_amount, Decimal('0.00'))
        self.assertTrue(credit.metadata.get('revoked'))

    def test_cleanup_credit_reservations_releases_stale_pending_allocations(self):
        credit = issue_billing_credit(
            chama=self.chama,
            amount='1500.00',
            source_type=BillingCredit.REFERRAL,
            source_reference='cleanup-test-001',
            description='Cleanup reservation test',
        )
        invoice_context = create_checkout_invoice(
            chama=self.chama,
            plan=self.pro_plan,
            provider=Subscription.MANUAL,
            billing_cycle=Subscription.MONTHLY,
            customer_email='',
            payment_metadata={'provider': Subscription.MANUAL},
        )
        invoice = invoice_context['invoice']
        Invoice.objects.filter(id=invoice.id).update(
            created_at=timezone.now() - timedelta(minutes=45)
        )

        result = cleanup_credit_reservations()

        self.assertEqual(result['released_invoices'], 1)
        self.assertEqual(result['released_allocations'], 1)
        credit.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(credit.remaining_amount, Decimal('1500.00'))
        self.assertEqual(invoice.status, Invoice.VOID)
        self.assertTrue(
            BillingCreditAllocation.objects.filter(
                invoice=invoice,
                status=BillingCreditAllocation.RELEASED,
            ).exists()
        )

    def test_credit_expiry_reminders_notify_once_per_credit_window(self):
        issue_billing_credit(
            chama=self.chama,
            amount='800.00',
            source_type=BillingCredit.REFERRAL,
            source_reference='expiry-test-001',
            description='Expiring soon credit',
            expires_at=timezone.now() + timedelta(days=3),
        )

        first_sent = send_credit_expiry_reminders()
        second_sent = send_credit_expiry_reminders()

        self.assertEqual(first_sent, 1)
        self.assertEqual(second_sent, 1)
        self.assertEqual(
            Notification.objects.filter(
                chama=self.chama,
                subject='Billing credit expiring soon',
            ).count(),
            1,
        )

    def test_online_checkout_confirmation_endpoint_rejects_non_manual_providers(self):
        response = self.client.post(
            '/api/v1/billing/checkout/confirm/',
            {
                'plan_id': self.pro_plan.id,
                'billing_cycle': 'monthly',
                'provider': Subscription.STRIPE,
                'session_id': 'cs_live_like_001',
                'chama_id': str(self.chama.id),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data['error'],
            'Online checkout confirmations are processed by provider webhooks.',
        )

        subscription = Subscription.objects.get(chama=self.chama)
        self.assertEqual(subscription.plan.code, Plan.FREE)
        self.assertEqual(subscription.status, Subscription.TRIALING)
        self.assertFalse(get_access_status(self.chama)['is_paid'])

    def test_stripe_webhook_activates_once_and_is_idempotent(self):
        stripe_provider = Mock()
        stripe_provider.handle_webhook.return_value = {
            'event': 'payment_succeeded',
            'data': {
                'id': 'cs_webhook_001',
                'metadata': {
                    'chama_id': str(self.chama.id),
                    'plan_id': str(self.pro_plan.id),
                    'billing_cycle': 'yearly',
                },
            },
        }

        with patch('apps.billing.payments.PaymentProviderFactory.get_provider', return_value=stripe_provider):
            first_response = self.client.post(
                '/api/v1/billing/webhooks/stripe/',
                data='{}',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='test_signature',
            )
            second_response = self.client.post(
                '/api/v1/billing/webhooks/stripe/',
                data='{}',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='test_signature',
            )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)

        subscription = Subscription.objects.get(chama=self.chama)
        self.assertEqual(subscription.plan, self.pro_plan)
        self.assertEqual(subscription.status, Subscription.ACTIVE)
        self.assertEqual(subscription.provider, Subscription.STRIPE)
        self.assertEqual(subscription.provider_subscription_id, 'cs_webhook_001')
        self.assertEqual(Subscription.objects.filter(chama=self.chama).count(), 1)
        self.assertTrue(get_access_status(self.chama)['is_paid'])
        self.assertEqual(Invoice.objects.filter(chama=self.chama, status=Invoice.PAID).count(), 1)

    def test_downgrade_is_blocked_when_current_usage_exceeds_new_plan_limit(self):
        self.client.post(
            '/api/v1/billing/checkout/confirm/',
            {
                'plan_id': self.pro_plan.id,
                'billing_cycle': 'monthly',
                'provider': Subscription.MANUAL,
                'session_id': 'manual_session_limit',
                'chama_id': str(self.chama.id),
            },
            format='json',
        )

        for index in range(26):
            member = User.objects.create_user(
                phone=f'+25471111{index:04d}',
                password='testpass123',
                full_name=f'Member {index}',
            )
            Membership.objects.create(
                user=member,
                chama=self.chama,
                role=MembershipRole.MEMBER,
                status=MemberStatus.ACTIVE,
                is_active=True,
                is_approved=True,
                approved_by=self.user,
                approved_at=timezone.now(),
            )

        free_plan = Plan.objects.get(code=Plan.FREE)
        response = self.client.post(
            '/api/v1/billing/subscription/change/',
            {
                'plan_id': free_plan.id,
                'billing_cycle': 'monthly',
                'chama_id': str(self.chama.id),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('member limit', response.data['error'])

    def test_mpesa_webhook_activates_invoice_only_after_verified_callback(self):
        invoice_context = create_checkout_invoice(
            chama=self.chama,
            plan=self.pro_plan,
            provider=Subscription.MPESA,
            billing_cycle=Subscription.MONTHLY,
            customer_email=self.user.email or '',
            provider_transaction_id='ws_CO_TEST_001',
            payment_metadata={
                'billing_cycle': Subscription.MONTHLY,
                'provider': Subscription.MPESA,
                'phone': self.user.phone,
            },
        )
        invoice = invoice_context['invoice']

        mpesa_provider = Mock()
        mpesa_provider.handle_webhook.return_value = {
            'event': 'payment_succeeded',
            'data': {
                'id': 'ws_CO_TEST_001',
                'payment_reference': 'MPESA12345',
                'amount': invoice.total_amount,
                'metadata': {'checkout_request_id': 'ws_CO_TEST_001'},
                'raw': {'Body': {'stkCallback': {'CheckoutRequestID': 'ws_CO_TEST_001'}}},
            },
        }

        with (
            patch('apps.payments.services.PaymentWorkflowService.verify_callback_request', return_value=(True, 'ok')),
            patch('apps.billing.payments.PaymentProviderFactory.get_provider', return_value=mpesa_provider),
        ):
            response = self.client.post(
                '/api/v1/billing/webhooks/mpesa/',
                data='{}',
                content_type='application/json',
                HTTP_X_MPESA_SIGNATURE='valid',
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        invoice.refresh_from_db()
        subscription = Subscription.objects.get(chama=self.chama)
        self.assertEqual(invoice.status, Invoice.PAID)
        self.assertEqual(subscription.plan, self.pro_plan)
        self.assertEqual(subscription.provider, Subscription.MPESA)
        self.assertEqual(subscription.provider_subscription_id, 'ws_CO_TEST_001')

    def test_payment_retries_do_not_bypass_stk_meter_limits(self):
        activate = self.client.post(
            '/api/v1/billing/checkout/confirm/',
            {
                'plan_id': self.pro_plan.id,
                'billing_cycle': 'monthly',
                'provider': Subscription.MANUAL,
                'session_id': 'manual_session_retry_limit',
                'chama_id': str(self.chama.id),
            },
            format='json',
        )
        self.assertEqual(activate.status_code, status.HTTP_200_OK)

        subscription = Subscription.objects.get(chama=self.chama)
        subscription.provider = Subscription.MPESA
        subscription.status = Subscription.PAST_DUE
        subscription.auto_renew = True
        subscription.grace_period_ends_at = timezone.now() + timedelta(days=3)
        subscription.payment_metadata = encrypt_billing_metadata({'phone': self.user.phone})
        subscription.failed_payment_count = 0
        subscription.save(
            update_fields=[
                'provider',
                'status',
                'auto_renew',
                'grace_period_ends_at',
                'payment_metadata',
                'failed_payment_count',
                'updated_at',
            ]
        )
        set_usage(self.chama, 'stk_pushes', 2500)

        with patch('apps.billing.payments.PaymentProviderFactory.create_checkout') as mocked_checkout:
            processed = process_payment_retries()

        self.assertEqual(processed, 1)
        mocked_checkout.assert_not_called()
        subscription.refresh_from_db()
        self.assertEqual(subscription.failed_payment_count, 1)

    def test_legacy_mpesa_initiation_honors_stk_limit(self):
        set_usage(self.chama, 'stk_pushes', 100)

        response = self.client.post(
            f'/api/v1/payments/{self.chama.id}/initiate',
            {
                'phone': self.user.phone,
                'amount': '100.00',
                'purpose': 'contribution',
                'reference': str(self.contribution_type.id),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertIn('M-Pesa STK allocation', response.data['detail'])
        self.assertEqual(MpesaTransaction.objects.filter(chama=self.chama).count(), 0)

    def test_otp_sms_respects_billing_quota_before_provider_send(self):
        UserPreference.objects.create(user=self.user, active_chama=self.chama)
        set_usage(self.chama, 'otp_sms', 100)
        otp_token, plain_code = OTPService.generate_otp(
            self.user.phone,
            user=self.user,
            purpose=OTPPurpose.LOGIN_2FA,
            delivery_method='sms',
        )

        with patch('apps.accounts.services.send_sms_message') as mocked_sms:
            with self.assertRaises(OTPDeliveryError):
                OTPService.send_otp(
                    self.user.phone,
                    otp_token,
                    plain_code,
                    user=self.user,
                )

        mocked_sms.assert_not_called()

    def test_renewal_reminders_notify_billing_contacts_once_per_cycle_window(self):
        activate = self.client.post(
            '/api/v1/billing/checkout/confirm/',
            {
                'plan_id': self.pro_plan.id,
                'billing_cycle': 'monthly',
                'provider': Subscription.MANUAL,
                'session_id': 'manual_session_reminder',
                'chama_id': str(self.chama.id),
            },
            format='json',
        )
        self.assertEqual(activate.status_code, status.HTTP_200_OK)

        subscription = Subscription.objects.get(chama=self.chama)
        subscription.current_period_end = timezone.now() + timedelta(days=3)
        subscription.save(update_fields=['current_period_end', 'updated_at'])

        sent = send_renewal_reminders()

        self.assertEqual(sent, 1)
        self.assertTrue(
            Notification.objects.filter(
                chama=self.chama,
                subject='Subscription renewal reminder',
            ).exists()
        )
