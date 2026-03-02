"""
Load Testing Scripts for Digital Chama System
Uses Locust for comprehensive API load testing
"""

from locust import HttpUser, task, between, tag
from locust.contrib.fasthttp import FastHttpUser
import json
import random
import string


class ChamaUser(FastHttpUser):
    """Base user class for Digital Chama load testing"""

    wait_time = between(1, 3)
    host = "https://your-domain.com"  # Change this to your domain

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.token = None
        self.chama_id = None
        self.membership_id = None

    def on_start(self):
        """Login and setup user session"""
        self.login()

    def login(self):
        """Authenticate user and get token"""
        # Generate random phone for testing
        phone = f"+2547{random.randint(10000000, 99999999)}"

        # Register user
        register_data = {
            "phone": phone,
            "full_name": f"Load Test User {random.randint(1, 10000)}",
            "password": "testpass123"
        }

        with self.client.post("/api/v1/auth/register/", json=register_data, catch_response=True) as response:
            if response.status_code == 201:
                # Get OTP (in real scenario, you'd need to handle OTP)
                otp_data = {"phone": phone, "otp": "123456"}  # Test OTP
                with self.client.post("/api/v1/auth/verify-otp/", json=otp_data, catch_response=True) as otp_response:
                    if otp_response.status_code == 200:
                        self.token = otp_response.json().get("access")
                        self.client.headers.update({"Authorization": f"Bearer {self.token}"})
                    else:
                        response.failure(f"OTP verification failed: {otp_response.status_code}")
            else:
                response.failure(f"Registration failed: {response.status_code}")


class BasicUser(ChamaUser):
    """Basic user operations - registration, login, profile"""

    @tag("auth")
    @task(2)
    def test_registration_flow(self):
        """Test complete registration flow"""
        phone = f"+2547{random.randint(10000000, 99999999)}"
        register_data = {
            "phone": phone,
            "full_name": f"Test User {random.randint(1, 1000)}",
            "password": "testpass123"
        }

        with self.client.post("/api/v1/auth/register/", json=register_data, catch_response=True) as response:
            if response.status_code not in [201, 400]:  # 400 is expected for duplicate
                response.failure(f"Unexpected registration response: {response.status_code}")

    @tag("auth")
    @task(3)
    def test_login_flow(self):
        """Test login flow"""
        # This would require existing users in the database
        login_data = {
            "phone": "+254700000000",  # Use a test user that exists
            "password": "testpass123"
        }

        with self.client.post("/api/v1/auth/login/", json=login_data, catch_response=True) as response:
            if response.status_code == 200:
                data = response.json()
                if "access" in data:
                    self.token = data["access"]
                    self.client.headers.update({"Authorization": f"Bearer {self.token}"})
                else:
                    response.failure("Login successful but no access token")
            elif response.status_code != 401:  # 401 is expected for invalid credentials
                response.failure(f"Unexpected login response: {response.status_code}")

    @tag("profile")
    @task(1)
    def test_profile_access(self):
        """Test profile access"""
        if not self.token:
            return

        with self.client.get("/api/v1/auth/profile/", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"Profile access failed: {response.status_code}")


class ChamaMember(ChamaUser):
    """Chama member operations - deposits, loans, meetings"""

    def on_start(self):
        super().on_start()
        self.join_chama()

    def join_chama(self):
        """Join a test chama"""
        # This assumes a test chama exists with join_code
        join_data = {
            "join_code": "TEST123"  # Use a test join code
        }

        with self.client.post("/api/v1/chama/join/", json=join_data, catch_response=True) as response:
            if response.status_code == 200:
                # Get chama details
                with self.client.get("/api/v1/chama/my-chama/", catch_response=True) as chama_response:
                    if chama_response.status_code == 200:
                        self.chama_id = chama_response.json().get("id")
            else:
                # If join fails, try to get existing membership
                with self.client.get("/api/v1/chama/my-memberships/", catch_response=True) as membership_response:
                    if membership_response.status_code == 200:
                        memberships = membership_response.json()
                        if memberships:
                            self.chama_id = memberships[0].get("chama", {}).get("id")

    @tag("finance")
    @task(5)
    def test_contribution_payment(self):
        """Test contribution payment flow"""
        if not self.chama_id:
            return

        # Initiate M-Pesa payment
        payment_data = {
            "chama_id": self.chama_id,
            "amount": random.randint(500, 5000),
            "payment_type": "contribution"
        }

        with self.client.post("/api/v1/payments/mpesa/stk-push/", json=payment_data, catch_response=True) as response:
            if response.status_code not in [200, 400]:  # 400 might be for validation
                response.failure(f"Payment initiation failed: {response.status_code}")

    @tag("finance")
    @task(2)
    def test_loan_application(self):
        """Test loan application"""
        if not self.chama_id:
            return

        loan_data = {
            "chama_id": self.chama_id,
            "amount": random.randint(10000, 50000),
            "purpose": "Business expansion",
            "duration_months": random.randint(3, 12)
        }

        with self.client.post("/api/v1/finance/loans/apply/", json=loan_data, catch_response=True) as response:
            if response.status_code not in [201, 400, 403]:  # Various valid responses
                response.failure(f"Loan application failed: {response.status_code}")

    @tag("meetings")
    @task(1)
    def test_meeting_access(self):
        """Test meeting access"""
        if not self.chama_id:
            return

        with self.client.get(f"/api/v1/meetings/?chama_id={self.chama_id}", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"Meeting access failed: {response.status_code}")

    @tag("reports")
    @task(1)
    def test_reports_access(self):
        """Test reports access"""
        if not self.chama_id:
            return

        with self.client.get(f"/api/v1/reports/financial-summary/?chama_id={self.chama_id}", catch_response=True) as response:
            if response.status_code not in [200, 403]:  # 403 is ok for non-treasurers
                response.failure(f"Reports access failed: {response.status_code}")


class ChamaAdmin(ChamaUser):
    """Chama admin operations - approvals, management"""

    @tag("admin")
    @task(3)
    def test_membership_approvals(self):
        """Test membership approval workflow"""
        if not self.chama_id:
            return

        # Get pending memberships
        with self.client.get(f"/api/v1/chama/memberships/?chama_id={self.chama_id}&status=pending", catch_response=True) as response:
            if response.status_code == 200:
                memberships = response.json().get("results", [])
                if memberships:
                    # Approve a random membership
                    membership_id = random.choice(memberships)["id"]
                    approval_data = {"status": "approved"}

                    with self.client.patch(f"/api/v1/chama/memberships/{membership_id}/", json=approval_data, catch_response=True) as approval_response:
                        if approval_response.status_code not in [200, 403]:  # 403 ok if not admin
                            approval_response.failure(f"Membership approval failed: {approval_response.status_code}")

    @tag("admin")
    @task(2)
    def test_loan_approvals(self):
        """Test loan approval workflow"""
        if not self.chama_id:
            return

        # Get pending loans
        with self.client.get(f"/api/v1/finance/loans/?chama_id={self.chama_id}&status=review", catch_response=True) as response:
            if response.status_code == 200:
                loans = response.json().get("results", [])
                if loans:
                    # Review a random loan
                    loan_id = random.choice(loans)["id"]
                    review_data = {"decision": "approved"}

                    with self.client.post(f"/api/v1/finance/loans/{loan_id}/review/", json=review_data, catch_response=True) as review_response:
                        if review_response.status_code not in [200, 403]:  # 403 ok if not treasurer
                            review_response.failure(f"Loan review failed: {review_response.status_code}")


class AIUser(ChamaUser):
    """AI feature testing"""

    @tag("ai")
    @task(2)
    def test_ai_chat(self):
        """Test AI chat functionality"""
        if not self.chama_id:
            return

        chat_data = {
            "chama_id": self.chama_id,
            "mode": "general",
            "message": "What is the current chama balance?"
        }

        with self.client.post("/api/v1/ai/chat/", json=chat_data, catch_response=True) as response:
            if response.status_code not in [200, 429]:  # 429 is rate limiting
                response.failure(f"AI chat failed: {response.status_code}")

    @tag("ai")
    @task(1)
    def test_ai_loan_prediction(self):
        """Test AI loan default prediction"""
        if not self.chama_id:
            return

        with self.client.post(f"/api/v1/ai/loan-default-prediction/?chama_id={self.chama_id}", catch_response=True) as response:
            if response.status_code not in [200, 429, 403]:  # Various valid responses
                response.failure(f"AI loan prediction failed: {response.status_code}")


class HealthCheckUser(FastHttpUser):
    """Health check and monitoring user"""

    wait_time = between(5, 15)

    @tag("health")
    @task
    def health_check(self):
        """Test health check endpoint"""
        with self.client.get("/health/", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"Health check failed: {response.status_code}")

    @tag("health")
    @task
    def api_health_check(self):
        """Test API health check"""
        with self.client.get("/api/v1/health/", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"API health check failed: {response.status_code}")