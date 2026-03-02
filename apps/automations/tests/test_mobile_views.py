"""
Unit Tests for Automation Mobile Views
Tests the new automation API endpoints
"""

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.finance.models import Contribution, ContributionGoal, ContributionSchedule


class EffectiveRoleViewTest(TestCase):
    """Test effective role endpoint"""
    
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone='+254700000001',
            password='testpass123'
        )
        self.chama = Chama.objects.create(
            name='Test Chama',
            contribution_amount=1000
        )
        self.membership = Membership.objects.create(
            user=self.user,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE
        )
    
    def test_get_effective_role_authenticated(self):
        """Test getting effective role as authenticated user"""
        self.client.force_authenticate(user=self.user)
        url = f'/api/v1/automations/effective-role/{self.membership.id}/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('effective_role', response.data)
    
    def test_get_effective_role_unauthenticated(self):
        """Test getting effective role without authentication"""
        url = f'/api/v1/automations/effective-role/{self.membership.id}/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ComplianceViewTest(TestCase):
    """Test compliance score endpoint"""
    
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone='+254700000002',
            password='testpass123'
        )
        self.chama = Chama.objects.create(
            name='Test Chama',
            contribution_amount=1000
        )
        self.membership = Membership.objects.create(
            user=self.user,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE
        )
    
    def test_get_compliance_missing_params(self):
        """Test compliance without required params"""
        self.client.force_authenticate(user=self.user)
        url = '/api/v1/automations/compliance/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
    def test_get_compliance_success(self):
        """Test getting compliance score"""
        self.client.force_authenticate(user=self.user)
        url = f'/api/v1/automations/compliance/?member_id={self.membership.id}&chama_id={self.chama.id}'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('on_time_percentage', response.data)
        self.assertIn('grade', response.data)


class LoanEligibilityViewTest(TestCase):
    """Test loan eligibility endpoint"""
    
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone='+254700000003',
            password='testpass123'
        )
        self.chama = Chama.objects.create(
            name='Test Chama',
            contribution_amount=1000,
            max_loan_amount=500000,
            min_loan_amount=1000,
            loan_interest_rate=12
        )
        self.membership = Membership.objects.create(
            user=self.user,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE
        )
    
    def test_loan_eligibility_missing_params(self):
        """Test loan eligibility without required params"""
        self.client.force_authenticate(user=self.user)
        url = '/api/v1/automations/loans/eligibility/'
        response = self.client.post(url, {}, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
    def test_loan_eligibility_success(self):
        """Test loan eligibility check"""
        self.client.force_authenticate(user=self.user)
        url = '/api/v1/automations/loans/eligibility/'
        data = {
            'member_id': str(self.membership.id),
            'chama_id': str(self.chama.id),
            'amount': 50000,
            'term_months': 6
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('eligible', response.data)
        self.assertIn('max_loan_amount', response.data)
        self.assertIn('risk_score', response.data)


class SecurityAlertsViewTest(TestCase):
    """Test security alerts endpoint"""
    
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone='+254700000004',
            password='testpass123'
        )
        self.chama = Chama.objects.create(
            name='Test Chama',
            contribution_amount=1000
        )
        self.membership = Membership.objects.create(
            user=self.user,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE
        )
    
    def test_security_alerts_missing_chama(self):
        """Test security alerts without chama_id"""
        self.client.force_authenticate(user=self.user)
        url = '/api/v1/automations/security/alerts/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
    def test_security_alerts_success(self):
        """Test getting security alerts"""
        self.client.force_authenticate(user=self.user)
        url = f'/api/v1/automations/security/alerts/?chama_id={self.chama.id}'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('alerts', response.data)


class AnomalyDetectionViewTest(TestCase):
    """Test anomaly detection endpoint"""
    
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone='+254700000005',
            password='testpass123'
        )
        self.chama = Chama.objects.create(
            name='Test Chama',
            contribution_amount=1000
        )
        self.membership = Membership.objects.create(
            user=self.user,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE
        )
    
    def test_check_withdrawal_anomaly(self):
        """Test withdrawal anomaly detection"""
        self.client.force_authenticate(user=self.user)
        url = '/api/v1/automations/anomaly/withdrawal/'
        data = {
            'member_id': str(self.membership.id),
            'chama_id': str(self.chama.id),
            'amount': 100000  # Large amount
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('is_anomaly', response.data)
    
    def test_get_anomalies(self):
        """Test getting anomalies"""
        self.client.force_authenticate(user=self.user)
        url = f'/api/v1/automations/anomaly/{self.chama.id}/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('anomalies', response.data)


class AuditLogsViewTest(TestCase):
    """Test audit logs endpoint"""
    
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone='+254700000006',
            password='testpass123'
        )
        self.chama = Chama.objects.create(
            name='Test Chama',
            contribution_amount=1000
        )
        self.membership = Membership.objects.create(
            user=self.user,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE
        )
    
    def test_audit_logs_missing_chama(self):
        """Test audit logs without chama_id"""
        self.client.force_authenticate(user=self.user)
        url = '/api/v1/automations/audit/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
    def test_audit_logs_success(self):
        """Test getting audit logs"""
        self.client.force_authenticate(user=self.user)
        url = f'/api/v1/automations/audit/?chama_id={self.chama.id}'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)


class PermissionCheckViewTest(TestCase):
    """Test permission check endpoint"""
    
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone='+254700000007',
            password='testpass123'
        )
        self.chama = Chama.objects.create(
            name='Test Chama',
            contribution_amount=1000
        )
        self.membership = Membership.objects.create(
            user=self.user,
            chama=self.chama,
            role=MembershipRole.TREASURER,
            status=MemberStatus.ACTIVE
        )
    
    def test_check_permission_success(self):
        """Test checking permission"""
        self.client.force_authenticate(user=self.user)
        url = '/api/v1/automations/check-permission/'
        data = {
            'membership_id': str(self.membership.id),
            'required_permission': 'view_wallet'
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('allowed', response.data)


class LoanApprovalQueueViewTest(TestCase):
    """Test loan approval queue endpoint"""
    
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone='+254700000008',
            password='testpass123'
        )
        self.chama = Chama.objects.create(
            name='Test Chama',
            contribution_amount=1000
        )
        self.membership = Membership.objects.create(
            user=self.user,
            chama=self.chama,
            role=MembershipRole.TREASURER,
            status=MemberStatus.ACTIVE
        )
    
    def test_loan_queue_missing_chama(self):
        """Test loan queue without chama_id"""
        self.client.force_authenticate(user=self.user)
        url = '/api/v1/automations/loans/queue/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
    def test_loan_queue_success(self):
        """Test getting loan approval queue"""
        self.client.force_authenticate(user=self.user)
        url = f'/api/v1/automations/loans/queue/?chama_id={self.chama.id}'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)


class DeviceSessionsViewTest(TestCase):
    """Test device sessions endpoint"""
    
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone='+254700000009',
            password='testpass123'
        )
    
    def test_device_sessions_unauthenticated(self):
        """Test device sessions without auth"""
        url = f'/api/v1/automations/security/devices/{self.user.id}/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
