"""
Chama Lifecycle & Member Management Automations

Production-grade automations for:
- Chama ID & invite link generator
- Invite SMS dispatcher
- Join request/approval/rejection workflows
- Member limit enforcer
- Inactive member flagger/escalator
- Chama health score calculator
- Anniversary notifier
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Q, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.chama.models import Chama, Membership


@dataclass
class ChamaInvite:
    """Generated invite for a chama."""
    invite_id: str
    invite_code: str
    invite_link: str
    qr_code_data: str
    expires_at: str | None


def generate_chama_invite(chama: "Chama", expires_days: int = 30) -> ChamaInvite:
    """Generate unique invite ID and shareable link for chama."""
    invite_id = str(uuid.uuid4())
    invite_code = secrets.token_urlsafe(8).upper()[:8]
    
    base_url = getattr(settings, "BASE_URL", "https://my-cham-a.app")
    invite_link = f"{base_url}/invite/{chama.id}/{invite_code}/"
    
    qr_data = f"{invite_link}|{chama.name}|{invite_code}"
    
    expires_at = None
    if expires_days:
        expires_at = (timezone.now() + timedelta(days=expires_days)).isoformat()
    
    logger.info(
        "Invite generated for chama %s: code=%s, expires=%s",
        chama.id,
        invite_code,
        expires_days,
    )
    
    return ChamaInvite(
        invite_id=invite_id,
        invite_code=invite_code,
        invite_link=invite_link,
        qr_code_data=qr_data,
        expires_at=expires_at,
    )


def send_invite_sms(user: "User", chama: "Chama", invite_link: str) -> bool:
    """Send invite SMS via Africa's Talking."""
    from apps.notifications.services import NotificationService
    
    message = (
        f"You are invited to join {chama.name} on MyChama! "
        f"Click to join: {invite_link} "
        f"This invite expires in 30 days."
    )
    
    try:
        NotificationService.send_notification(
            user=user,
            message=message,
            channels=["sms"],
            notification_type="invitation",
            priority="high",
        )
        logger.info("Invite SMS sent to %s for chama %s", user.id, chama.id)
        return True
    except Exception as exc:
        logger.error(f"Failed to send invite SMS: {exc}")
        return False


def notify_join_request(chama: "Chama", applicant: "User") -> None:
    """Notify Chairperson of new join request."""
    from apps.chama.models import Membership, MembershipRole
    from apps.notifications.services import NotificationService
    
    chairperson = Membership.objects.filter(
        chama=chama,
        role=MembershipRole.CHAMA_ADMIN,
        is_active=True,
    ).select_related("user").first()
    
    if chairperson and chairperson.user:
        message = (
            f"New join request from {applicant.full_name or applicant.phone}. "
            f"Phone: {applicant.phone}. "
            f"Review in app."
        )
        
        try:
            NotificationService.send_notification(
                user=chairperson.user,
                message=message,
                channels=["push", "sms"],
                notification_type="join_request",
                priority="high",
            )
        except Exception as exc:
            logger.error(f"Failed to notify Chairperson: {exc}")


def send_member_welcome(
    membership: "Membership",
    chama: "Chama",
    rules_summary: str | None = None,
) -> None:
    """Send welcome message on member approval."""
    from apps.notifications.services import NotificationService
    
    message = (
        f"Welcome to {chama.name}! "
        f"You are now a member. "
        f"Your first contribution is due on the next cycle date."
    )
    
    if rules_summary:
        message += f" Key rules: {rules_summary}"
    
    message += (
        f" Download the app for updates or call {chama.phone or 'us'} for help."
    )
    
    try:
        NotificationService.send_notification(
            user=membership.user,
            message=message,
            channels=["push", "sms"],
            notification_type="welcome",
            priority="high",
        )
        logger.info("Welcome sent to member %s for chama %s", membership.id, chama.id)
    except Exception as exc:
        logger.error(f"Failed to send welcome: {exc}")


def notify_member_rejection(
    user: "User",
    chama: "Chama",
    reason: str,
) -> None:
    """Notify applicant of rejection with reason."""
    from apps.notifications.services import NotificationService
    
    message = (
        f"Your request to join {chama.name} was not approved. "
        f"Reason: {reason}. "
        f"You may reapply after addressing this concern."
    )
    
    try:
        NotificationService.send_notification(
            user=user,
            message=message,
            channels=["push", "sms"],
            notification_type="rejection",
            priority="normal",
        )
    except Exception as exc:
        logger.error(f"Failed to notify rejection: {exc}")


def check_member_limit(chama: "Chama") -> dict:
    """Check member count against max limit."""
    from apps.chama.models import Membership, MembershipStatus
    
    current_count = Membership.objects.filter(
        chama=chama,
        status=MembershipStatus.ACTIVE,
        is_active=True,
    ).count()
    
    max_members = getattr(chama, "max_members", 50)
    is_at_limit = current_count >= max_members
    spots_remaining = max(0, max_members - current_count)
    
    if is_at_limit:
        from apps.notifications.services import NotificationService
        from apps.chama.models import MembershipRole
        
        chairperson = Membership.objects.filter(
            chama=chama,
            role=MembershipRole.CHAMA_ADMIN,
        ).select_related("user").first()
        
        if chairperson and chairperson.user:
            message = (
                f"{chama.name} has reached maximum member limit ({max_members}). "
                f"New join requests are blocked until a member exits."
            )
            
            try:
                NotificationService.send_notification(
                    user=chairperson.user,
                    message=message,
                    channels=["push"],
                    notification_type="system",
                    priority="medium",
                )
            except Exception:
                pass
    
    return {
        "chama_id": str(chama.id),
        "current_count": current_count,
        "max_members": max_members,
        "is_at_limit": is_at_limit,
        "spots_remaining": spots_remaining,
        "can_accept_new": not is_at_limit,
    }


def flag_inactive_member(
    membership: "Membership",
    missed_contributions: int,
) -> dict:
    """Flag member as inactive after 3+ missed contributions."""
    from apps.notifications.services import NotificationService
    from apps.chama.models import Membership, MembershipRole, MembershipStatus
    
    if missed_contributions < 3:
        return {"flagged": False, "reason": "Below threshold"}
    
    inactive_key = f"inactive:{membership.id}"
    if cache.get(inactive_key):
        return {"flagged": True, "already_flagged": True, "days_flagged": 0}
    
    cache.set(inactive_key, True, timeout=1209600)
    
    treasurer = Membership.objects.filter(
        chama=membership.chama,
        role=MembershipRole.TREASURER,
        status=MembershipStatus.ACTIVE,
    ).select_related("user").first()
    
    if treasurer and treasurer.user:
        message = (
            f"Alert: {membership.user.full_name} has missed "
            f"{missed_contributions} consecutive contributions. "
            f"Flagged for follow-up."
        )
        
        try:
            NotificationService.send_notification(
                user=treasurer.user,
                message=message,
                channels=["push"],
                notification_type="inactivity",
                priority="high",
            )
        except Exception:
            pass
    
    return {
        "flagged": True,
        "member_id": str(membership.id),
        "missed_contributions": missed_contributions,
        "flagged_at": timezone.now().isoformat(),
    }


def escalate_inactive_member(
    membership: "Membership",
    inactive_days: int,
) -> dict:
    """Escalate inactive member to Chairperson after 14 days."""
    from apps.notifications.services import NotificationService
    from apps.chama.models import Membership, MembershipRole
    
    if inactive_days < 14:
        return {"escalated": False, "reason": "Below threshold"}
    
    chairperson = Membership.objects.filter(
        chama=membership.chama,
        role=MembershipRole.CHAMA_ADMIN,
    ).select_related("user").first()
    
    if chairperson and chairperson.user:
        message = (
            f"Escalation: {membership.user.full_name} has been inactive "
            f"for {inactive_days} days. "
            f"Consider suspension or exit process."
        )
        
        try:
            NotificationService.send_notification(
                user=chairperson.user,
                message=message,
                channels=["push", "sms"],
                notification_type="inactivity",
                priority="high",
            )
        except Exception as exc:
            logger.error(f"Failed to escalate inactive member: {exc}")
    
    return {
        "escalated": True,
        "member_id": str(membership.id),
        "days_inactive": inactive_days,
        "escalated_to": "chairperson",
    }


@dataclass
class ChamaHealthScore:
    """Chama health score result."""
    chama_id: str
    score: int
    rating: str
    collection_rate: float
    loan_repayment_rate: float
    meeting_attendance: float
    governance_score: float
    trend: str
    alert_level: str


def calculate_chama_health_score(chama: "Chama", days: int = 90) -> ChamaHealthScore:
    """Calculate comprehensive chama health score."""
    from apps.finance.models import Loan, LoanStatus, Payment, PaymentStatus
    from apps.meetings.models import Meeting
    
    today = timezone.now().date()
    lookback = today - timedelta(days=days)
    
    collection_rate = 0.0
    loan_repayment_rate = 0.0
    meeting_attendance = 0.0
    governance_score = 100.0
    
    completed_payments = Payment.objects.filter(
        chama=chama,
        status=PaymentStatus.COMPLETED,
        created_at__gte=lookback,
    ).count()
    
    expected_payments = chama.memberships.filter(
        is_active=True,
    ).count() * max(1, days // 30)
    
    if expected_payments > 0:
        collection_rate = min(100.0, (completed_payments / expected_payments) * 100)
    
    active_loans = Loan.objects.filter(
        chama=chama,
        status=LoanStatus.ACTIVE,
    )
    npl_count = 0
    total_loans = active_loans.count()
    
    if total_loans > 0:
        threshold = today - timedelta(days=30)
        npl_count = active_loans.filter(
            next_due_date__lte=threshold,
        ).count()
        loan_repayment_rate = max(0.0, 100.0 - (npl_count / total_loans) * 100)
    
    meetings = Meeting.objects.filter(
        chama=chama,
        date__gte=lookback,
    )
    total_meetings = meetings.count()
    
    if total_meetings > 0:
        from apps.meetings.models import AttendanceRecord
        total_attendance = 0
        for meeting in meetings:
            total_attendance += meeting.attendance_records.filter(
                status="present",
            ).count()
        
        expected_attendance = total_meetings * chama.memberships.filter(
            is_active=True,
        ).count()
        
        if expected_attendance > 0:
            meeting_attendance = min(
                100.0,
                (total_attendance / expected_attendance) * 100,
            )
    
    par_ratio = max(0.0, 100.0 - loan_repayment_rate)
    
    overall_score = int(
        collection_rate * 0.30 +
        loan_repayment_rate * 0.30 +
        meeting_attendance * 0.20 +
        (100.0 - par_ratio) * 0.20,
    )
    
    rating = (
        "AAA" if overall_score >= 90 else
        "AA" if overall_score >= 80 else
        "A" if overall_score >= 70 else
        "BB" if overall_score >= 60 else
        "B" if overall_score >= 50 else "C"
    )
    
    alert_level = (
        "HIGH" if overall_score < 50 else
        "MEDIUM" if overall_score < 70 else
        "LOW" if overall_score < 85 else "NONE"
    )
    
    return ChamaHealthScore(
        chama_id=str(chama.id),
        score=overall_score,
        rating=rating,
        collection_rate=collection_rate,
        loan_repayment_rate=loan_repayment_rate,
        meeting_attendance=meeting_attendance,
        governance_score=governance_score,
        trend="stable",
        alert_level=alert_level,
    )


def notify_chama_anniversary(chama: "Chama") -> None:
    """Notify members of chama anniversary."""
    from apps.notifications.services import NotificationService
    from apps.chama.models import Membership, MembershipStatus
    
    if not chama.created_at:
        return
    
    today = timezone.now().date()
    anniversary = chama.created_at.date().replace(year=today.year)
    
    if anniversary < today:
        anniversary = anniversary.replace(year=today.year + 1)
    
    days_until = (anniversary - today).days
    
    if days_until != 0:
        return
    
    years_active = today.year - chama.created_at.date().year
    
    message = (
        f"Happy {years_active}-year anniversary to {chama.name}! "
        f"Thank you for being part of this journey. "
        f"Here's to many more years of financial growth together!"
    )
    
    members = Membership.objects.filter(
        chama=chama,
        status=MembershipStatus.ACTIVE,
    ).select_related("user")
    
    for membership in members:
        try:
            NotificationService.send_notification(
                user=membership.user,
                message=message,
                channels=["push", "sms"],
                notification_type="anniversary",
                priority="normal",
            )
        except Exception:
            pass
    
    logger.info("Anniversary notifications sent for chama %s", chama.id)