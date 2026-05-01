"""Tests for Payout workflow."""

from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.chama.models import Chama, Membership
from apps.finance.models import Wallet, WalletOwnerType, Penalty
from apps.governance.models import ApprovalRequest, ApprovalStatus, ApprovalType
from apps.issues.models import Issue

from .models import (
    Payout,
    PayoutRotation,
    PayoutStatus,
    EligibilityStatus,
    PayoutAuditLog,
    PayoutEligibilityCheck,
)
from .services import PayoutService

User = get_user_model()


class PayoutServiceTestCase(TestCase):
    """Test PayoutService methods."""

    def setUp(self):
        """Set up test data."""
        # Create test users
        self.treasurer_user = User.objects.create_user(
            phone="+254712345678",
            password="testpass123",
            full_name="John Treasurer",
        )
        self.chairperson_user = User.objects.create_user(
            phone="+254712345679",
            password="testpass123",
            full_name="Jane Chairperson",
        )
        self.member_user = User.objects.create_user(
            phone="+254712345680",
            password="testpass123",
            full_name="Bob Member",
        )

        # Create chama
        self.chama = Chama.objects.create(
            name="Test Chama",
            description="Test chama for payouts",
            status="active",
        )

        # Create memberships
        self.treasurer_membership = Membership.objects.create(
            user=self.treasurer_user,
            chama=self.chama,
            role="TREASURER",
            status="active",
            is_active=True,
            is_approved=True,
            approved_by=self.treasurer_user,
            approved_at=timezone.now(),
        )
        self.chairperson_membership = Membership.objects.create(
            user=self.chairperson_user,
            chama=self.chama,
            role="CHAIRPERSON",
            status="active",
            is_active=True,
            is_approved=True,
            approved_by=self.treasurer_user,
            approved_at=timezone.now(),
        )
        self.member_membership = Membership.objects.create(
            user=self.member_user,
            chama=self.chama,
            role="MEMBER",
            status="active",
            is_active=True,
            is_approved=True,
            approved_by=self.treasurer_user,
            approved_at=timezone.now(),
        )

        # Create wallet
        self.wallet = Wallet.objects.create(
            owner_type=WalletOwnerType.CHAMA,
            owner_id=str(self.chama.id),
            available_balance=Decimal("50000.00"),
            locked_balance=Decimal("0.00"),
            currency="KES",
        )

        # Set up rotation
        self.rotation, _ = PayoutRotation.objects.get_or_create(
            chama=self.chama,
            defaults={
                "current_position": 0,
                "rotation_cycle": 1,
                "members_in_rotation": [str(self.member_membership.id)],
            },
        )

    def test_trigger_payout(self):
        """Test triggering a new payout."""
        payout = PayoutService.trigger_payout(
            chama_id=self.chama.id,
            member_id=self.member_membership.id,
            amount=Decimal("5000.00"),
            trigger_type="manual",
            triggered_by_id=self.treasurer_user.id,
        )

        self.assertIsNotNone(payout)
        self.assertEqual(payout.status, PayoutStatus.TRIGGERED)
        self.assertEqual(payout.amount, Decimal("5000.00"))
        self.assertEqual(payout.member, self.member_membership)

        # Check audit log
        audit_log = PayoutAuditLog.objects.filter(
            payout=payout,
            action="TRIGGERED",
        ).first()
        self.assertIsNotNone(audit_log)

    def test_eligibility_check_eligible(self):
        """Test eligibility check for eligible member."""
        payout = PayoutService.trigger_payout(
            chama_id=self.chama.id,
            member_id=self.member_membership.id,
            amount=Decimal("5000.00"),
            triggered_by_id=self.treasurer_user.id,
        )

        payout, eligibility_check = PayoutService.check_eligibility(payout.id)

        self.assertEqual(payout.eligibility_status, EligibilityStatus.ELIGIBLE)
        self.assertEqual(eligibility_check.result, EligibilityStatus.ELIGIBLE)
        self.assertEqual(payout.status, PayoutStatus.AWAITING_TREASURER_REVIEW)

    def test_eligibility_check_with_penalties(self):
        """Test eligibility check fails due to outstanding penalties."""
        # Add a penalty
        Penalty.objects.create(
            chama=self.chama,
            member=self.member_user,
            amount=Decimal("1000.00"),
            amount_paid=Decimal("0.00"),
            reason="Late payment",
            due_date=timezone.now().date(),
            status="unpaid",
            created_by=self.treasurer_user,
            updated_by=self.treasurer_user,
        )

        payout = PayoutService.trigger_payout(
            chama_id=self.chama.id,
            member_id=self.member_membership.id,
            amount=Decimal("5000.00"),
            triggered_by_id=self.treasurer_user.id,
        )

        payout, eligibility_check = PayoutService.check_eligibility(payout.id)

        self.assertEqual(payout.eligibility_status, EligibilityStatus.PENDING_PENALTIES)
        self.assertTrue(eligibility_check.has_outstanding_penalties)
        self.assertEqual(payout.status, PayoutStatus.INELIGIBLE)

    def test_eligibility_check_inactive_member(self):
        """Test eligibility check fails for inactive member."""
        self.member_membership.status = "suspended"
        self.member_membership.save()

        payout = PayoutService.trigger_payout(
            chama_id=self.chama.id,
            member_id=self.member_membership.id,
            amount=Decimal("5000.00"),
            triggered_by_id=self.treasurer_user.id,
        )

        payout, eligibility_check = PayoutService.check_eligibility(payout.id)

        self.assertEqual(payout.eligibility_status, EligibilityStatus.INACTIVE_MEMBER)
        self.assertFalse(eligibility_check.member_is_active)
        self.assertEqual(payout.status, PayoutStatus.INELIGIBLE)

    def test_skip_to_next_member(self):
        """Test skipping ineligible member."""
        payout = PayoutService.trigger_payout(
            chama_id=self.chama.id,
            member_id=self.member_membership.id,
            amount=Decimal("5000.00"),
            triggered_by_id=self.treasurer_user.id,
        )

        payout, _ = PayoutService.check_eligibility(payout.id)

        # Suspend member
        self.member_membership.status = "suspended"
        self.member_membership.save()

        # Re-check eligibility
        payout, _ = PayoutService.check_eligibility(payout.id)

        # Skip to next member
        payout = PayoutService.skip_to_next_member(
            payout.id,
            reason="Member suspended",
            actor_id=self.treasurer_user.id,
        )

        self.assertEqual(payout.status, PayoutStatus.INELIGIBLE)
        self.assertEqual(payout.skip_reason, "Member suspended")

    def test_defer_to_next_cycle(self):
        """Test deferring payout to next cycle."""
        payout = PayoutService.trigger_payout(
            chama_id=self.chama.id,
            member_id=self.member_membership.id,
            amount=Decimal("5000.00"),
            triggered_by_id=self.treasurer_user.id,
        )

        payout = PayoutService.defer_to_next_cycle(
            payout.id,
            reason="Awaiting member contribution",
            actor_id=self.treasurer_user.id,
        )

        self.assertEqual(payout.status, PayoutStatus.INELIGIBLE)
        self.assertEqual(payout.defer_reason, "Awaiting member contribution")

    def test_treasurer_approval_flow(self):
        """Test treasurer review and approval."""
        payout = PayoutService.trigger_payout(
            chama_id=self.chama.id,
            member_id=self.member_membership.id,
            amount=Decimal("5000.00"),
            triggered_by_id=self.treasurer_user.id,
        )

        payout, _ = PayoutService.check_eligibility(payout.id)

        # Send to treasurer review
        payout = PayoutService.send_to_treasurer_review(payout.id)
        self.assertEqual(payout.status, PayoutStatus.AWAITING_TREASURER_REVIEW)
        self.assertIsNotNone(payout.approval_request)

        # Treasurer approves
        payout = PayoutService.treasurer_approve(payout.id, self.treasurer_user.id)
        self.assertEqual(payout.status, PayoutStatus.AWAITING_CHAIR_APPROVAL)
        self.assertEqual(payout.treasurer_reviewed_by, self.treasurer_user)

    def test_treasurer_rejection(self):
        """Test treasurer rejection."""
        payout = PayoutService.trigger_payout(
            chama_id=self.chama.id,
            member_id=self.member_membership.id,
            amount=Decimal("5000.00"),
            triggered_by_id=self.treasurer_user.id,
        )

        payout, _ = PayoutService.check_eligibility(payout.id)
        payout = PayoutService.send_to_treasurer_review(payout.id)

        # Treasurer rejects
        payout = PayoutService.treasurer_reject(
            payout.id,
            "Insufficient documentation",
            self.treasurer_user.id,
        )

        self.assertEqual(payout.status, PayoutStatus.TREASURY_REJECTED)
        self.assertEqual(
            payout.treasurer_rejection_reason,
            "Insufficient documentation",
        )

    def test_chairperson_approval_flow(self):
        """Test chairperson approval."""
        payout = PayoutService.trigger_payout(
            chama_id=self.chama.id,
            member_id=self.member_membership.id,
            amount=Decimal("5000.00"),
            triggered_by_id=self.treasurer_user.id,
        )

        payout, _ = PayoutService.check_eligibility(payout.id)
        payout = PayoutService.send_to_treasurer_review(payout.id)
        payout = PayoutService.treasurer_approve(payout.id, self.treasurer_user.id)

        # Chairperson approves
        payout = PayoutService.chairperson_approve(payout.id, self.chairperson_user.id)

        self.assertEqual(payout.status, PayoutStatus.APPROVED)
        self.assertEqual(payout.chairperson_approved_by, self.chairperson_user)
        self.assertIsNotNone(payout.chairperson_approved_at)

    def test_chairperson_rejection(self):
        """Test chairperson rejection."""
        payout = PayoutService.trigger_payout(
            chama_id=self.chama.id,
            member_id=self.member_membership.id,
            amount=Decimal("5000.00"),
            triggered_by_id=self.treasurer_user.id,
        )

        payout, _ = PayoutService.check_eligibility(payout.id)
        payout = PayoutService.send_to_treasurer_review(payout.id)
        payout = PayoutService.treasurer_approve(payout.id, self.treasurer_user.id)

        # Chairperson rejects
        payout = PayoutService.chairperson_reject(
            payout.id,
            "Requires more discussion",
            self.chairperson_user.id,
        )

        self.assertEqual(payout.status, PayoutStatus.CHAIR_REJECTED)
        self.assertEqual(
            payout.chairperson_rejection_reason,
            "Requires more discussion",
        )

    def test_flag_and_release_hold(self):
        """Test flagging payout on hold and releasing."""
        payout = PayoutService.trigger_payout(
            chama_id=self.chama.id,
            member_id=self.member_membership.id,
            amount=Decimal("5000.00"),
            triggered_by_id=self.treasurer_user.id,
        )

        # Flag on hold
        payout = PayoutService.flag_payout_on_hold(
            payout.id,
            "Awaiting member verification",
            self.treasurer_user.id,
        )

        self.assertTrue(payout.is_on_hold)
        self.assertEqual(payout.status, PayoutStatus.HOLD)
        self.assertEqual(
            payout.hold_reason,
            "Awaiting member verification",
        )

        # Release from hold
        payout = PayoutService.release_payout_from_hold(
            payout.id,
            self.treasurer_user.id,
            "Verification complete",
        )

        self.assertFalse(payout.is_on_hold)
        self.assertEqual(payout.status, PayoutStatus.APPROVED)

    def test_payment_success_workflow(self):
        """Test successful payment completion."""
        payout = PayoutService.trigger_payout(
            chama_id=self.chama.id,
            member_id=self.member_membership.id,
            amount=Decimal("5000.00"),
            triggered_by_id=self.treasurer_user.id,
        )

        payout, _ = PayoutService.check_eligibility(payout.id)
        payout = PayoutService.send_to_treasurer_review(payout.id)
        payout = PayoutService.treasurer_approve(payout.id, self.treasurer_user.id)
        payout = PayoutService.chairperson_approve(payout.id, self.chairperson_user.id)

        # Simulate wallet payout completion
        from apps.payments.unified_models import PaymentMethod
        from apps.payments.unified_payment_service import UnifiedPaymentService

        payment_intent = UnifiedPaymentService.create_payment_intent(
            chama=self.chama,
            user=self.member_user,
            amount=payout.amount,
            payment_method=PaymentMethod.CASH,
            purpose="other",
            purpose_id=payout.id,
            description="Payout",
        )

        payout.payment_intent = payment_intent
        payout.save()

        # Handle success
        payout = PayoutService.handle_payment_success(payment_intent.id)

        self.assertEqual(payout.status, PayoutStatus.SUCCESS)
        self.assertIsNotNone(payout.payment_completed_at)
        self.assertIsNotNone(payout.ledger_entry)

    def test_payment_failure_and_retry(self):
        """Test payment failure and retry."""
        payout = PayoutService.trigger_payout(
            chama_id=self.chama.id,
            member_id=self.member_membership.id,
            amount=Decimal("5000.00"),
            triggered_by_id=self.treasurer_user.id,
        )

        payout, _ = PayoutService.check_eligibility(payout.id)
        payout = PayoutService.send_to_treasurer_review(payout.id)
        payout = PayoutService.treasurer_approve(payout.id, self.treasurer_user.id)
        payout = PayoutService.chairperson_approve(payout.id, self.chairperson_user.id)

        from apps.payments.unified_payment_service import UnifiedPaymentService
        from apps.payments.unified_models import PaymentMethod

        payment_intent = UnifiedPaymentService.create_payment_intent(
            chama=self.chama,
            user=self.member_user,
            amount=payout.amount,
            payment_method=PaymentMethod.MPESA,
            purpose="other",
            purpose_id=payout.id,
            description="Payout",
        )

        payout.payment_intent = payment_intent
        payout.save()

        # Handle failure
        payout = PayoutService.handle_payment_failure(
            payment_intent.id,
            "Network timeout",
            "TIMEOUT",
        )

        self.assertEqual(payout.status, PayoutStatus.PROCESSING)  # Will retry
        self.assertEqual(payout.retry_count, 1)
        self.assertTrue(payout.can_retry())

        # Simulate more failures
        PayoutService.handle_payment_failure(payment_intent.id, "Network timeout", "TIMEOUT")
        PayoutService.handle_payment_failure(payment_intent.id, "Network timeout", "TIMEOUT")
        payout.refresh_from_db()

        # Max retries exceeded
        self.assertEqual(payout.retry_count, 3)
        self.assertFalse(payout.can_retry())
        self.assertEqual(payout.status, PayoutStatus.FAILED)

    def test_rotation_advancement(self):
        """Test rotation advancement after successful payout."""
        initial_position = self.rotation.current_position

        payout = PayoutService.trigger_payout(
            chama_id=self.chama.id,
            member_id=self.member_membership.id,
            amount=Decimal("5000.00"),
            triggered_by_id=self.treasurer_user.id,
        )

        payout, _ = PayoutService.check_eligibility(payout.id)
        payout = PayoutService.send_to_treasurer_review(payout.id)
        payout = PayoutService.treasurer_approve(payout.id, self.treasurer_user.id)
        payout = PayoutService.chairperson_approve(payout.id, self.chairperson_user.id)

        from apps.payments.unified_payment_service import UnifiedPaymentService
        from apps.payments.unified_models import PaymentMethod

        payment_intent = UnifiedPaymentService.create_payment_intent(
            chama=self.chama,
            user=self.member_user,
            amount=payout.amount,
            payment_method=PaymentMethod.CASH,
            purpose="other",
            purpose_id=payout.id,
            description="Payout",
        )

        payout.payment_intent = payment_intent
        payout.save()

        PayoutService.handle_payment_success(payment_intent.id)

        self.rotation.refresh_from_db()
        self.assertEqual(self.rotation.current_position, initial_position + 1)
