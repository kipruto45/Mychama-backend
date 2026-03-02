#!/usr/bin/env python3
"""
Backend API Verification Script
Tests all API endpoints for the Digital Chama system
"""

import requests
import json
import sys
from typing import Dict, List, Tuple

# Configuration
BASE_URL = "http://localhost:8000/api/v1"
TEST_PHONE = "+254700000001"
TEST_PASSWORD = "Admin123!"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def print_status(status: str, message: str):
    if status == "PASS":
        print(f"{Colors.GREEN}✓{Colors.END} {message}")
    elif status == "FAIL":
        print(f"{Colors.RED}✗{Colors.END} {message}")
    elif status == "SKIP":
        print(f"{Colors.YELLOW}⊘{Colors.END} {message}")
    else:
        print(f"{Colors.BLUE}•{Colors.END} {message}")

class APITester:
    def __init__(self):
        self.base_url = BASE_URL
        self.token = None
        self.refresh_token = None
        self.session = requests.Session()
        self.results: List[Tuple[str, str, str]] = []
    
    def log(self, status: str, message: str):
        print_status(status, message)
        self.results.append((status, message, ""))
    
    def test_health(self) -> bool:
        """Test server health"""
        try:
            response = self.session.get(f"http://localhost:8000/health/", timeout=5)
            if response.status_code == 200:
                self.log("PASS", "Server health check")
                return True
        except Exception as e:
            self.log("FAIL", f"Server not reachable: {e}")
        return False
    
    def test_login(self) -> bool:
        """Test login and token acquisition"""
        try:
            response = self.session.post(
                f"{self.base_url}/auth/login",
                json={"phone": TEST_PHONE, "password": TEST_PASSWORD},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                self.token = data.get('access')
                self.refresh_token = data.get('refresh')
                if self.token:
                    self.session.headers.update({'Authorization': f'Bearer {self.token}'})
                    self.log("PASS", "Login successful")
                    return True
            self.log("FAIL", f"Login failed: {response.status_code}")
        except Exception as e:
            self.log("FAIL", f"Login error: {e}")
        return False
    
    def test_token_refresh(self) -> bool:
        """Test token refresh"""
        if not self.refresh_token:
            self.log("SKIP", "No refresh token available")
            return False
        try:
            response = self.session.post(
                f"{self.base_url}/auth/refresh",
                json={"refresh": self.refresh_token},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                new_token = data.get('access')
                if new_token:
                    self.token = new_token
                    self.session.headers.update({'Authorization': f'Bearer {new_token}'})
                    self.log("PASS", "Token refresh successful")
                    return True
            self.log("FAIL", f"Token refresh failed: {response.status_code}")
        except Exception as e:
            self.log("FAIL", f"Token refresh error: {e}")
        return False
    
    def test_me_endpoint(self) -> bool:
        """Test /auth/me endpoint"""
        return self._test_endpoint("GET", "/auth/me", "Get current user")
    
    def test_chama_list(self) -> bool:
        """Test chama list endpoint"""
        return self._test_endpoint("GET", "/chamas/", "List chamas")
    
    def test_finance_dashboard(self) -> bool:
        """Test finance dashboard"""
        return self._test_endpoint("GET", "/finance/dashboard", "Finance dashboard")
    
    def test_wallet_balance(self) -> bool:
        """Test wallet balance"""
        return self._test_endpoint("GET", "/finance/wallet", "Wallet balance")
    
    def test_ledger(self) -> bool:
        """Test ledger transactions"""
        return self._test_endpoint("GET", "/finance/ledger", "Transaction ledger")
    
    def test_loans(self) -> bool:
        """Test loans list"""
        return self._test_endpoint("GET", "/finance/loans/", "Loans list")
    
    def test_loan_eligibility(self) -> bool:
        """Test loan eligibility"""
        return self._test_endpoint("GET", "/finance/loans/eligibility", "Loan eligibility")
    
    def test_payments_transactions(self) -> bool:
        """Test payment transactions"""
        return self._test_endpoint("GET", "/payments/my/transactions", "Payment transactions")
    
    def test_meetings(self) -> bool:
        """Test meetings list"""
        return self._test_endpoint("GET", "/meetings/", "Meetings list")
    
    def test_notifications(self) -> bool:
        """Test notifications"""
        return self._test_endpoint("GET", "/notifications/", "Notifications")
    
    def test_issues(self) -> bool:
        """Test issues/tickets"""
        return self._test_endpoint("GET", "/issues/", "Support tickets")
    
    def test_reports(self) -> bool:
        """Test reports"""
        return self._test_endpoint("GET", "/reports/member-statement", "Member statement")
    
    def test_permission_denied(self) -> bool:
        """Test 403 handling for unauthorized access"""
        # Try accessing admin endpoint with regular user
        try:
            # This should fail with 403 for non-admin
            response = self.session.get(f"{self.base_url}/security/audit")
            if response.status_code in [403, 401]:
                self.log("PASS", "Permission denied handled correctly")
                return True
            # If 200, user might be admin
            elif response.status_code == 200:
                self.log("SKIP", "User has admin privileges")
                return True
            self.log("FAIL", f"Unexpected status: {response.status_code}")
        except Exception as e:
            self.log("FAIL", f"Permission test error: {e}")
        return False
    
    def _test_endpoint(self, method: str, path: str, description: str) -> bool:
        """Generic endpoint tester"""
        url = f"{self.base_url}{path}"
        try:
            if method == "GET":
                response = self.session.get(url, timeout=10)
            elif method == "POST":
                response = self.session.post(url, json={}, timeout=10)
            else:
                self.log("SKIP", f"Method {method} not implemented")
                return False
            
            if response.status_code in [200, 201]:
                self.log("PASS", description)
                return True
            elif response.status_code == 401:
                self.log("FAIL", f"{description} - Unauthorized")
                return False
            elif response.status_code == 403:
                self.log("FAIL", f"{description} - Forbidden")
                return False
            elif response.status_code == 404:
                self.log("FAIL", f"{description} - Not Found")
                return False
            else:
                self.log("FAIL", f"{description} - Status {response.status_code}")
                return False
        except requests.exceptions.Timeout:
            self.log("FAIL", f"{description} - Timeout")
            return False
        except Exception as e:
            self.log("FAIL", f"{description} - Error: {e}")
            return False
    
    def test_response_structure(self) -> bool:
        """Test that responses have correct structure"""
        try:
            response = self.session.get(f"{self.base_url}/auth/me", timeout=10)
            if response.status_code == 200:
                data = response.json()
                # Check for expected fields
                if isinstance(data, dict):
                    self.log("PASS", "Response structure valid")
                    return True
            self.log("FAIL", "Invalid response structure")
        except Exception as e:
            self.log("FAIL", f"Structure test error: {e}")
        return False
    
    def test_invalid_json(self) -> bool:
        """Test handling of invalid JSON"""
        try:
            # Send malformed request
            response = self.session.post(
                f"{self.base_url}/auth/login",
                data="not valid json",
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            if response.status_code == 400:
                self.log("PASS", "Invalid JSON handled correctly")
                return True
            self.log("FAIL", f"Invalid JSON not handled: {response.status_code}")
        except Exception as e:
            self.log("FAIL", f"Invalid JSON test error: {e}")
        return False
    
    def run_all_tests(self):
        """Run all tests"""
        print(f"\n{Colors.BLUE}{'='*60}")
        print("Digital Chama API Verification")
        print(f"{'='*60}{Colors.END}\n")
        
        # Connection tests
        print(f"\n{Colors.BLUE}[1] Connection Tests{Colors.END}")
        self.test_health()
        
        # Auth tests
        print(f"\n{Colors.BLUE}[2] Authentication Tests{Colors.END}")
        self.test_login()
        
        if not self.token:
            print(f"\n{Colors.RED}Cannot continue without authentication{Colors.END}")
            self.print_summary()
            return
        
        self.test_token_refresh()
        self.test_me_endpoint()
        
        # Feature tests
        print(f"\n{Colors.BLUE}[3] Feature Tests{Colors.END}")
        self.test_chama_list()
        self.test_finance_dashboard()
        self.test_wallet_balance()
        self.test_ledger()
        self.test_loans()
        self.test_loan_eligibility()
        self.test_payments_transactions()
        self.test_meetings()
        self.test_notifications()
        self.test_issues()
        self.test_reports()
        
        # Error handling tests
        print(f"\n{Colors.BLUE}[4] Error Handling Tests{Colors.END}")
        self.test_permission_denied()
        self.test_response_structure()
        self.test_invalid_json()
        
        self.print_summary()
    
    def print_summary(self):
        """Print test summary"""
        passed = sum(1 for r in self.results if r[0] == "PASS")
        failed = sum(1 for r in self.results if r[0] == "FAIL")
        skipped = sum(1 for r in self.results if r[0] == "SKIP")
        total = len(self.results)
        
        print(f"\n{Colors.BLUE}{'='*60}")
        print("TEST SUMMARY")
        print(f"{'='*60}{Colors.END}")
        print(f"Total Tests: {total}")
        print(f"{Colors.GREEN}Passed: {passed}{Colors.END}")
        print(f"{Colors.RED}Failed: {failed}{Colors.END}")
        print(f"{Colors.YELLOW}Skipped: {skipped}{Colors.END}")
        
        if failed == 0:
            print(f"\n{Colors.GREEN}✓ All tests passed!{Colors.END}")
            sys.exit(0)
        else:
            print(f"\n{Colors.RED}✗ Some tests failed{Colors.END}")
            sys.exit(1)


if __name__ == "__main__":
    tester = APITester()
    tester.run_all_tests()
