"""
Contribution Automations

Production-grade automations for:
- Cycle auto-opener based on frequency
- Contribution reminders (3 days, 1 day, due date)
- M-Pesa STK push initiator
- M-Pesa callback handler
- Late payment flagger
- Penalty auto-calculator & applier
"""

from __future__ import annotations

import hashlib
import logging
import random
import string
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from apps.chama.models import Chama, Membership


@dataclass
class CycleInfo:
    """Contribution cycle info."""
    cycle_id: str
    cycle_number: int
    due_date: str
    amount: Decimal
    grace_period_days: int
    late_penalty_rate: Decimal
    status: str


@dataclass
class ContributionReminder:
    """Contribution reminder details."""
    member_id: str
    member_name: str
    phone: str
    days_until_due: int
    amount_due: Decimal
    reminder_type: str
    channels: list[str]


@dataclass
class PaymentConfirmation:
    """Payment confirmation result."""
    is_confirmed: bool
    payment_id: str | None
    amount: Decimal
    reference: str
    credited_to: str
    message: str


def open_new_contribution_cycle(
    chama: "Chama",
    frequency: str = "monthly",
    custom_day: int | None = None,
) -> CycleInfo:
    """Open new contribution cycle based on frequency."""
    from apps.finance.models import ContributionCycle, ContributionCycleStatus
    
    today = timezone.now().date()
    
    if frequency == "weekly":
        due_date = today + timedelta(days=7)
    elif frequency == "biweekly":
        due_date = today + timedelta(days=14)
    elif frequency == "monthly" and custom_day:
        day = min(custom_day, 28)
        due_date = today.replace(day=day)
        if due_date <= today:
            due_date = (today + timedelta(days=30)).replace(day=day)
    else:
        due_date = today + timedelta(days=30)
    
    grace_period = getattr(chama, "grace_period_days", 3)
    penalty_rate = Decimal(str(getattr(chama, "late_penalty_rate", "2.0")))
    
    last_cycle = ContributionCycle.objects.filter(
        chama=chama,
    ).order_by("-cycle_number").first()
    
    cycle_number = (last_cycle.cycle_number + 1) if last_cycle else 1
    
    cycle = ContributionCycle.objects.create(
        chama=chama,
        cycle_number=cycle_number,
        due_date=due_date,
        contribution_amount=chama.contribution_amount,
        grace_period_days=grace_period,
        late_penalty_rate=penalty_rate,
        status=ContributionCycleStatus.OPEN,
        opened_at=timezone.now(),
    )
    
    logger.info(
        "Contribution cycle %s opened for chama %s: due=%s",
        cycle_number,
        chama.id,
        due_date,
    )
    
    return CycleInfo(
        cycle_id=str(cycle.id),
        cycle_number=cycle_number,
        due_date=str(due_date),
        amount=cycle.contribution_amount,
        grace_period_days=grace_period,
        late_penalty_rate=penalty_rate,
        status=cycle.status,
    )


def get_cycle_summary(
    chama: "Chama",
    cycle_id: str,
) -> dict:
    """Get contribution cycle summary for all members."""
    from apps.finance.models import ContributionCycle, ContributionSchedule, Payment, PaymentStatus
    
    try:
        cycle = ContributionCycle.objects.get(id=cycle_id, chama=chama)
    except ContributionCycle.DoesNotExist:
        return {}
    
    schedules = ContributionSchedule.objects.filter(
        cycle=cycle,
        is_active=True,
    )
    
    total_expected = schedules.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    
    payments = Payment.objects.filter(
        chama=chama,
        cycle=cycle,
        status=PaymentStatus.COMPLETED,
    )
    
    total_collected = payments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    members_paid = payments.values("member_id").distinct().count()
    total_members = schedules.count()
    
    collection_rate = 0.0
    if total_expected > 0:
        collection_rate = float(total_collected / total_expected) * 100
    
    return {
        "cycle_id": str(cycle.id),
        "cycle_number": cycle.cycle_number,
        "due_date": str(cycle.due_date),
        "total_expected": str(total_expected),
        "total_collected": str(total_collected),
        "members_paid": members_paid,
        "total_members": total_members,
        "collection_rate": collection_rate,
        "status": cycle.status,
    }


def generate_contribution_reminders(
    chama: "Chama",
    days_before: int,
    reminder_type: str,
) -> list[ContributionReminder]:
    """Generate contribution reminders for unpaid members."""
    from apps.finance.models import ContributionCycle, ContributionSchedule, Payment, PaymentStatus
    from apps.chama.models import Membership, MembershipStatus
    
    today = timezone.now().date()
    
    active_cycle = ContributionCycle.objects.filter(
        chama=chama,
        status="open",
    ).order_by("-cycle_number").first()
    
    if not active_cycle:
        return []
    
    due_date = active_cycle.due_date
    target_date = due_date - timedelta(days=days_before)
    
    if target_date != today:
        return []
    
    unpaid_members = Membership.objects.filter(
        chama=chama,
        status=MembershipStatus.ACTIVE,
        is_active=True,
    ).exclude(
        id__in=Payment.objects.filter(
            chama=chama,
            cycle=active_cycle,
            status=PaymentStatus.COMPLETED,
        ).values("member_id")
    ).select_related("user")
    
    reminders = []
    for membership in unpaid_members:
        reminders.append(ContributionReminder(
            member_id=str(membership.id),
            member_name=membership.user.full_name if membership.user else "Unknown",
            phone=membership.user.phone or "",
            days_until_due=days_before,
            amount_due=active_cycle.contribution_amount,
            reminder_type=reminder_type,
            channels=["push", "sms"],
        ))
    
    return reminders


def initiate_mpesa_stk_push(
    membership: "Membership",
    chama: "Chama",
    amount: Decimal,
    reference: str | None = None,
) -> dict:
    """Initiate M-Pesa STK push for payment."""
    from apps.payments.mpesa import initiate_stk_push
    
    if not membership.user.phone:
        raise ValueError("Member has no phone number")
    
    phone = membership.user.phone.replace("+", "").replace(" ", "")
    if not phone.startswith("254"):
        phone = "254" + phone.lstrip("0")
    
    if not reference:
        chars = string.ascii_uppercase + string.digits
        reference = "".join(random.choices(chars, k=8))
    
    try:
        result = initiate_stk_push(
            phone_number=phone,
            amount=float(amount),
            account_reference=reference,
            transaction_desc=f"Contribution to {chama.name}",
        )
        
        logger.info(
            "STK push initiated for %s: amount=%s, ref=%s",
            membership.user.id,
            amount,
            reference,
        )
        
        return {
            "success": True,
            "checkout_request_id": result.get("CheckoutRequestID", ""),
            "merchant_request_id": result.get("MerchantRequestID", ""),
            "reference": reference,
            "amount": str(amount),
        }
    
    except Exception as exc:
        logger.error(f"STK push failed: {exc}")
        return {
            "success": False,
            "error": str(exc),
            "reference": reference,
        }


def handle_mpesa_callback(
    chama: "Chama",
    checkout_request_id: str,
    merchant_request_id: str,
    result_code: int,
    result_desc: str,
    amount: Decimal,
    mpesa_receipt: str,
    balance: Decimal,
    transaction_date: str,
    payer_phone: str,
) -> PaymentConfirmation:
    """Handle M-Pesa callback and confirm/reject payment."""
    from apps.finance.models import (
        ContributionCycle,
        Payment,
        PaymentStatus,
        Wallet,
    )
    
    if result_code != 0:
        return PaymentConfirmation(
            is_confirmed=False,
            payment_id=None,
            amount=amount,
            reference=checkout_request_id,
            credited_to="",
            message=f"Payment failed: {result_desc}",
        )
    
    cycle = ContributionCycle.objects.filter(
        chama=chama,
        status="open",
    ).order_by("-cycle_number").first()
    
    if not cycle:
        return PaymentConfirmation(
            is_confirmed=False,
            payment_id=None,
            amount=amount,
            reference=mpesa_receipt,
            credited_to="",
            message="No active contribution cycle found",
        )
    
    payment = Payment.objects.create(
        chama=chama,
        cycle=cycle,
        member=membership if (membership := chama.memberships.filter(user__phone__contains=payer_phone[-9:]).first()) else None,
        amount=amount,
        payment_method="mpesa",
        mpesa_receipt_number=mpesa_receipt,
        status=PaymentStatus.COMPLETED,
        payment_date=timezone.now(),
        reference=checkout_request_id,
    )
    
    from apps.finance.services import FinanceService
    try:
        FinanceService.update_ledger_from_payment(payment)
    except Exception:
        pass
    
    logger.info(
        "Payment confirmed: id=%s, amount=%s, receipt=%s",
        payment.id,
        amount,
        mpesa_receipt,
    )
    
    return PaymentConfirmation(
        is_confirmed=True,
        payment_id=str(payment.id),
        amount=amount,
        reference=mpesa_receipt,
        credited_to=str(cycle.id),
        message="Payment confirmed. Thank you!",
    )


def generate_receipt(
    payment_id: str,
    output_format: str = "pdf",
) -> dict:
    """Generate PDF receipt for a payment."""
    from apps.finance.models import Payment
    
    try:
        payment = Payment.objects.get(id=payment_id)
    except Payment.DoesNotExist:
        return {"success": False, "error": "Payment not found"}
    
    receipt_data = {
        "receipt_number": f"RCP-{payment.id}",
        "date": str(payment.payment_date),
        "amount": str(payment.amount),
        "member": payment.member.full_name if payment.member else "Unknown",
        "chama": payment.chama.name,
        "method": payment.payment_method,
        "reference": payment.mpesa_receipt_number or payment.reference or "",
    }
    
    return {
        "success": True,
        "receipt_data": receipt_data,
        "format": output_format,
    }


def reconcile_bank_transfer(
    chama: "Chama",
    reference_code: str,
    amount: Decimal,
    deposit_date: str,
) -> dict:
    """Reconcile bank transfer with pending contribution."""
    from apps.finance.models import ContributionSchedule, Payment, PaymentStatus
    
    schedule = ContributionSchedule.objects.filter(
        chama=chama,
        reference_code__iexact=reference_code,
        is_active=True,
    ).first()
    
    if not schedule:
        return {
            "matched": False,
            "reason": "No pending contribution with this reference",
        }
    
    cycle = ContributionSchedule.objects.filter(
        chama=chama,
        is_active=True,
    ).first().cycle
    
    if not cycle:
        return {"matched": False, "reason": "No active cycle"}
    
    member = schedule.member
    from apps.chama.models import Membership
    membership = Membership.objects.filter(
        chama=chama,
        user=member,
    ).first()
    
    payment = Payment.objects.create(
        chama=chama,
        cycle=cycle,
        member=member,
        membership=membership,
        amount=amount,
        payment_method="bank_transfer",
        reference=reference_code,
        status=PaymentStatus.COMPLETED,
        payment_date=timezone.now(),
    )
    
    logger.info(
        "Bank transfer reconciled: ref=%s, amount=%s, payment=%s",
        reference_code,
        amount,
        payment.id,
    )
    
    return {
        "matched": True,
        "payment_id": str(payment.id),
        "member_id": str(member.id),
        "amount": str(amount),
    }


def flag_late_payment(
    membership: "Membership",
    cycle_id: str,
    grace_period_days: int,
) -> dict:
    """Flag member as late after grace period."""
    from apps.finance.models import ContributionCycle, ContributionSchedule
    
    if grace_period_days <= 0:
        return {"flagged": False, "reason": "No grace period"}
    
    try:
        cycle = ContributionCycle.objects.get(id=cycle_id)
    except ContributionCycle.DoesNotExist:
        return {"flagged": False, "reason": "Cycle not found"}
    
    today = timezone.now().date()
    grace_end = cycle.due_date + timedelta(days=grace_period_days)
    
    if today <= grace_end:
        return {"flagged": False, "reason": "Within grace period"}
    
    late_key = f"late:{membership.id}:{cycle_id}"
    if cache.get(late_key):
        return {"flagged": True, "already_flagged": True}
    
    cache.set(late_key, True, timeout=2592000)
    
    from apps.notifications.services import NotificationService
    from apps.chama.models import Membership, MembershipRole
    
    treasurer = Membership.objects.filter(
        chama=membership.chama,
        role=MembershipRole.TREASURER,
    ).select_related("user").first()
    
    if treasurer and treasurer.user:
        message = (
            f"{membership.user.full_name} is late on contribution "
            f"for cycle {cycle.cycle_number}. "
            f"Grace period ended {grace_end}."
        )
        
        try:
            NotificationService.send_notification(
                user=treasurer.user,
                message=message,
                channels=["push"],
                notification_type="late_payment",
                priority="high",
            )
        except Exception:
            pass
    
    return {
        "flagged": True,
        "member_id": str(membership.id),
        "cycle_id": str(cycle_id),
        "grace_end_date": str(grace_end),
    }


def calculate_late_penalty(
    amount: Decimal,
    days_late: int,
    penalty_rate: Decimal,
    max_penalty_percent: Decimal = Decimal("50.0"),
) -> Decimal:
    """Calculate late payment penalty."""
    if days_late <= 0 or penalty_rate <= 0:
        return Decimal("0.00")
    
    daily_rate = penalty_rate / 100 / 30
    penalty = amount * daily_rate * Decimal(days_late)
    
    max_penalty = amount * max_penalty_percent / 100
    penalty = min(penalty, max_penalty)
    
    return penalty.quantize(Decimal("1.00"))


def apply_late_penalty(
    membership: "Membership",
    cycle_id: str,
    penalty_amount: Decimal,
) -> dict:
    """Apply late penalty to ledger and notify."""
    from apps.finance.models import Fine, FineType
    
    if penalty_amount <= 0:
        return {"applied": False, "reason": "No penalty due"}
    
    fine = Fine.objects.create(
        chama=membership.chama,
        member=membership.user,
        fine_type=FineType.LATE_CONTRIBUTION,
        amount=penalty_amount,
        reason=f"Late contribution penalty for cycle {cycle_id}",
        cycle_id=cycle_id,
        status="pending",
    )
    
    from apps.notifications.services import NotificationService
    
    message = (
        f"Late payment penalty of KES {penalty_amount:,.2f} has been applied. "
        f"Please pay promptly to avoid further penalties."
    )
    
    try:
        NotificationService.send_notification(
            user=membership.user,
            message=message,
            channels=["push", "sms"],
            notification_type="fine",
            priority="high",
        )
    except Exception:
        pass
    
    logger.info(
        "Late penalty applied: member=%s, amount=%s",
        membership.id,
        penalty_amount,
    )
    
    return {
        "applied": True,
        "fine_id": str(fine.id),
        "amount": str(penalty_amount),
    }