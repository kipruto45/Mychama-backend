"""
AI Endpoint Validation Tests
"""
import json
import pytest
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.ai.models import AIConversation
from apps.chama.models import Chama, Membership


class AIEndpointValidationTests(APITestCase):
    """Test AI endpoints return valid JSON responses"""

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

    def _validate_ai_response(self, response_data, expected_fields=None):
        """Validate AI response has required structure"""
        self.assertIsInstance(response_data, dict)

        # Check for required fields based on endpoint
        if expected_fields:
            for field in expected_fields:
                self.assertIn(field, response_data)

        # Validate JSON serializability
        try:
            json.dumps(response_data)
        except (TypeError, ValueError) as e:
            self.fail(f"AI response is not JSON serializable: {e}")

        # Check for decision field in decision responses
        if "decision" in response_data:
            self.assertIsInstance(response_data["decision"], str)
            self.assertGreater(len(response_data["decision"]), 0)

        # Check confidence if present
        if "confidence" in response_data:
            self.assertIsInstance(response_data["confidence"], (int, float))
            self.assertGreaterEqual(response_data["confidence"], 0.0)
            self.assertLessEqual(response_data["confidence"], 1.0)

    def test_ai_chat_valid_json(self):
        """Test AI chat returns valid JSON"""
        url = reverse('ai-chat')
        data = {
            "chama_id": str(self.chama.id),
            "mode": "general",
            "message": "What is the current balance?"
        }
        self.client.force_authenticate(user=self.user)
        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self._validate_ai_response(response.data, ["answer", "citations"])

    def test_ai_membership_review_valid_json(self):
        """Test membership review returns valid JSON"""
        url = reverse('ai-membership-risk-scoring')
        data = {"chama_id": str(self.chama.id)}
        self.client.force_authenticate(user=self.user)
        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self._validate_ai_response(response.data, ["decision", "confidence"])

    def test_ai_loan_prediction_valid_json(self):
        """Test loan prediction returns valid JSON"""
        url = reverse('ai-loan-default-prediction')
        data = {"chama_id": str(self.chama.id)}
        self.client.force_authenticate(user=self.user)
        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self._validate_ai_response(response.data, ["decision", "confidence"])

    def test_ai_issue_triage_valid_json(self):
        """Test issue triage returns valid JSON"""
        # Create a test issue first
        from apps.issues.models import Issue
        issue = Issue.objects.create(
            chama=self.chama,
            title="Test Issue",
            description="Test description",
            reported_by=self.user,
            created_by=self.user
        )

        url = reverse('ai-issue-triage')
        data = {
            "issue_id": str(issue.id),
            "chama_id": str(self.chama.id)
        }
        self.client.force_authenticate(user=self.user)
        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self._validate_ai_response(response.data, ["decision", "confidence"])

    def test_ai_meeting_summary_valid_json(self):
        """Test meeting summary returns valid JSON"""
        # Create a test meeting
        from apps.meetings.models import Meeting
        meeting = Meeting.objects.create(
            chama=self.chama,
            title="Test Meeting",
            scheduled_at="2024-01-01T10:00:00Z",
            created_by=self.user
        )

        url = reverse('ai-meeting-summarize')
        data = {
            "meeting_id": str(meeting.id),
            "chama_id": str(self.chama.id)
        }
        self.client.force_authenticate(user=self.user)
        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self._validate_ai_response(response.data, ["summary"])

    def test_ai_report_explanation_valid_json(self):
        """Test report explanation returns valid JSON"""
        # Create a test report
        from apps.reports.models import ReportRun
        report = ReportRun.objects.create(
            chama=self.chama,
            report_type="financial_summary",
            status="completed",
            created_by=self.user
        )

        url = reverse('ai-report-explain')
        data = {
            "report_id": str(report.id),
            "chama_id": str(self.chama.id)
        }
        self.client.force_authenticate(user=self.user)
        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self._validate_ai_response(response.data, ["explanation"])