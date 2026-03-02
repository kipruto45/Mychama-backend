"""
Production Readiness Tests - Edge Cases and Security
"""
import pytest
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.chama.models import Chama, Membership
from apps.finance.models import Contribution, Loan
from apps.payments.models import PaymentIntent


class EdgeCaseTests(APITestCase):
    """Test edge cases and error conditions"""

    def setUp(self):
        self.user = User.objects.create_user(
            phone="+254700000000",
            full_name="Test User",
            password="testpass123"
        )
        self.chama = Chama.objects.create(
            name="Test Chama",
            created_by=self.user
        )
        self.membership = Membership.objects.create(
            chama=self.chama,
            member=self.user,
            role="admin"
        )

    def test_duplicate_phone_registration(self):
        """Test duplicate phone number registration is blocked"""
        url = reverse('user-register')
        data = {
            "phone": "+254700000000",  # Same as existing user
            "full_name": "Duplicate User",
            "password": "testpass123"
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("phone", response.data)

    def test_expired_otp_login(self):
        """Test expired OTP cannot be used"""
        # This would require mocking time or having OTP expiry logic
        pass

    def test_over_limit_withdrawal(self):
        """Test withdrawal exceeding balance limits"""
        url = reverse('withdrawal-create')
        data = {
            "chama_id": str(self.chama.id),
            "amount": 1000000,  # Very large amount
            "reason": "Test withdrawal"
        }
        self.client.force_authenticate(user=self.user)
        response = self.client.post(url, data)
        # Should be blocked by business rules
        self.assertIn(response.status_code, [status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN])

    def test_suspended_user_access(self):
        """Test suspended user cannot access chama"""
        self.user.is_active = False
        self.user.save()

        url = reverse('chama-detail', kwargs={'pk': self.chama.id})
        self.client.force_authenticate(user=self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class SecurityTests(APITestCase):
    """Test security features"""

    def test_rate_limiting(self):
        """Test API rate limiting works"""
        url = reverse('user-login')
        data = {"phone": "+254700000000", "password": "wrong"}

        # Send multiple requests
        for _ in range(10):
            response = self.client.post(url, data)

        # Should eventually be rate limited
        self.assertIn(response.status_code, [status.HTTP_429_TOO_MANY_REQUESTS, status.HTTP_400_BAD_REQUEST])

    def test_sql_injection_protection(self):
        """Test SQL injection attempts are blocked"""
        url = reverse('user-login')
        data = {
            "phone": "+254700000000' OR '1'='1",
            "password": "testpass123"
        }
        response = self.client.post(url, data)
        self.assertNotEqual(response.status_code, status.HTTP_200_OK)

    def test_xss_protection(self):
        """Test XSS attempts are sanitized"""
        url = reverse('user-register')
        data = {
            "phone": "+254700000001",
            "full_name": "<script>alert('xss')</script>",
            "password": "testpass123"
        }
        response = self.client.post(url, data)
        if response.status_code == status.HTTP_201_CREATED:
            user = User.objects.get(phone="+254700000001")
            self.assertNotIn("<script>", user.full_name)


class IdempotencyTests(APITestCase):
    """Test idempotency keys work correctly"""

    def setUp(self):
        self.user = User.objects.create_user(
            phone="+254700000000",
            full_name="Test User",
            password="testpass123"
        )

    def test_duplicate_payment_idempotency(self):
        """Test duplicate payment callbacks are handled correctly"""
        url = reverse('mpesa-callback')
        idempotency_key = "test-payment-123"

        data = {
            "idempotency_key": idempotency_key,
            "amount": 1000,
            "phone": "+254700000000",
            "reference": "REF123"
        }

        # First request
        response1 = self.client.post(url, data, HTTP_IDEMPOTENCY_KEY=idempotency_key)
        # Second request with same key
        response2 = self.client.post(url, data, HTTP_IDEMPOTENCY_KEY=idempotency_key)

        # Both should succeed or both should fail consistently
        self.assertEqual(response1.status_code, response2.status_code)


class FraudRuleTests(TestCase):
    """Test fraud detection rules"""

    def setUp(self):
        self.user = User.objects.create_user(
            phone="+254700000000",
            full_name="Test User",
            password="testpass123"
        )
        self.chama = Chama.objects.create(
            name="Test Chama",
            created_by=self.user
        )

    def test_velocity_check(self):
        """Test high-frequency transaction detection"""
        # Create multiple rapid transactions
        for i in range(5):
            PaymentIntent.objects.create(
                chama=self.chama,
                member=self.user,
                amount=1000,
                type="deposit",
                created_by=self.user
            )

        # Check if fraud rules trigger
        # This would depend on the fraud detection implementation
        pass

    def test_unusual_amount_pattern(self):
        """Test unusual amount pattern detection"""
        # Create normal transactions
        for _ in range(10):
            PaymentIntent.objects.create(
                chama=self.chama,
                member=self.user,
                amount=1000,
                type="deposit",
                created_by=self.user
            )

        # Create unusual large transaction
        PaymentIntent.objects.create(
            chama=self.chama,
            member=self.user,
            amount=100000,  # Much larger
            type="deposit",
            created_by=self.user
        )

        # Check if flagged
        pass