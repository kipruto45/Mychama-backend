from uuid import uuid4

from django.test import SimpleTestCase
from django.urls import reverse

from apps.fines.serializers import FineIssueSerializer


class FinesUrlTests(SimpleTestCase):
    def test_fines_routes_are_registered(self):
        self.assertEqual(reverse("api:v1:fine-overview"), "/api/v1/fines/overview/")
        self.assertEqual(reverse("api:v1:fine-auto-generate"), "/api/v1/fines/auto-generate/")
        self.assertEqual(reverse("api:v1:fine-category-list"), "/api/v1/fines/categories/")
        self.assertEqual(reverse("api:v1:fine-adjustment-list"), "/api/v1/fines/adjustments/")
        self.assertEqual(reverse("api:v1:fine-payment-list"), "/api/v1/fines/payments/")
        self.assertEqual(reverse("api:v1:fine-reminder-list"), "/api/v1/fines/reminders/")

    def test_fine_issue_serializer_accepts_uuid_member_ids(self):
        serializer = FineIssueSerializer(
            data={
                "member_ids": [str(uuid4())],
                "category": "CUSTOM",
                "amount": "250.00",
                "due_date": "2026-03-10",
                "reason": "Late attendance fine",
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
