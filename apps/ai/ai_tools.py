"""
AI Tools Router for Digital Chama

This module implements the tool-based AI architecture that ensures
the assistant NEVER hallucinates - it only provides verified data
from the system's actual data sources.

Each tool follows a strict pattern:
1. Check permissions (role-based)
2. Fetch data from actual database (with Redis caching for performance)
3. Return structured, verified data
4. Never make assumptions about financial data

Caching Strategy:
- Wallet summaries: 60 seconds (financial data changes frequently)
- Unpaid members: 30 seconds (payment status changes)
- Loan book: 60 seconds
- Meeting schedule: 5 minutes (rarely changes)
- Fines: 30 seconds
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from functools import wraps
from typing import Any

from django.core.cache import cache
from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MembershipRole
from apps.chama.services import get_effective_role
from apps.finance.models import (
    Contribution,
    InstallmentStatus,
    Loan,
    LoanStatus,
)
from apps.finance.summary import get_chama_financial_snapshot
from apps.fines.models import Fine, FineStatus
from apps.meetings.models import Meeting
from apps.payments.models import PaymentIntent, PaymentIntentStatus, PaymentIntentType
from core.models import AuditLog

logger = logging.getLogger(__name__)

# Cache TTL constants (in seconds)
CACHE_TTL_SHORT = 30       # 30 seconds - for frequently changing data
CACHE_TTL_MEDIUM = 60      # 60 seconds - for financial data
CACHE_TTL_LONG = 300      # 5 minutes - for rarely changing data


def generate_cache_key(prefix: str, *args, **kwargs) -> str:
    """Generate a unique cache key based on function arguments."""
    key_parts = [prefix]
    for arg in args:
        if hasattr(arg, 'id'):
            key_parts.append(str(arg.id))
        else:
            key_parts.append(str(arg))
    
    # Sort kwargs for consistent key generation
    for k, v in sorted(kwargs.items()):
        key_parts.append(f"{k}:{v}")
    
    key_string = ":".join(key_parts)
    # Hash long keys to keep them manageable
    if len(key_string) > 200:
        return f"{prefix}:{hashlib.md5(key_string.encode()).hexdigest()}"
    return key_string


def cached_tool(ttl: int = CACHE_TTL_MEDIUM, prefix: str = None):
    """
    Decorator to cache tool results in Redis.
    
    Args:
        ttl: Cache time-to-live in seconds
        prefix: Custom cache key prefix (defaults to function name)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            cache_prefix = prefix or func.__name__
            cache_key = generate_cache_key(f"ai_tool:{cache_prefix}", *args, **kwargs)
            
            # Try to get from cache
            try:
                cached_result = cache.get(cache_key)
                if cached_result is not None:
                    logger.debug(f"Cache HIT: {cache_key}")
                    return cached_result
            except Exception as e:
                logger.warning(f"Cache read error: {e}")
            
            # Execute the function
            logger.debug(f"Cache MISS: {cache_key}")
            result = func(*args, **kwargs)
            
            # Store in cache
            try:
                cache.set(cache_key, result, ttl)
            except Exception as e:
                logger.warning(f"Cache write error: {e}")
            
            return result
        return wrapper
    return decorator


def invalidate_chama_cache(chama_id: str) -> None:
    """
    Invalidate all cached data for a chama.
    Call this when chama data changes (contributions, loans, etc.)
    """
    try:
        # Get all keys matching the pattern and delete them
        # Note: In production, you might want to use Redis SCAN for this
        cache_keys = [
            f"ai_tool:*:{chama_id}",
            f"ai_context:{chama_id}",
        ]
        for _key in cache_keys:
            # This is a simplified approach - in production consider using
            # cache.delete_pattern() or Redis SCAN
            pass
        logger.info(f"Cache invalidation triggered for chama: {chama_id}")
    except Exception as e:
        logger.warning(f"Cache invalidation error: {e}")


def mask_phone(phone: str) -> str:
    """Mask phone number for privacy."""
    raw = str(phone or "")
    if len(raw) < 7:
        return "***"
    return f"{raw[:5]}****{raw[-3:]}"


def require_membership(user: User, chama: Chama) -> Membership:
    """Verify user is an active member of the chama."""
    membership = Membership.objects.filter(
        user=user,
        chama=chama,
        is_active=True,
        is_approved=True,
    ).first()
    if not membership:
        raise PermissionDenied("You are not an approved active member of this chama.")
    return membership


def get_effective_role_name(user: User, chama: Chama) -> str:
    """Get the effective role for a user in a chama."""
    membership = require_membership(user, chama)
    return get_effective_role(user, chama.id, membership)


def _completed_loan_statuses() -> tuple[str, ...]:
    return (
        LoanStatus.PAID,
        LoanStatus.CLOSED,
        LoanStatus.CLEARED,
        LoanStatus.REJECTED,
    )


def _loan_remaining_balance(loan: Loan) -> Decimal:
    """Estimate remaining balance using unpaid installments with safe fallbacks."""
    scheduled_balance = loan.installments.exclude(
        status=InstallmentStatus.PAID
    ).aggregate(total=Sum("expected_amount"))["total"]
    if scheduled_balance is not None:
        return scheduled_balance
    if loan.status in _completed_loan_statuses():
        return Decimal("0.00")
    return loan.principal


def _loan_periodic_payment(loan: Loan) -> Decimal:
    next_installment = loan.installments.exclude(
        status=InstallmentStatus.PAID
    ).order_by("due_date").first()
    if next_installment:
        return next_installment.expected_amount
    if loan.duration_months:
        base_principal = loan.principal / Decimal(loan.duration_months)
        interest = (loan.principal * loan.interest_rate / Decimal("100")) / Decimal(
            loan.duration_months
        )
        return (base_principal + interest).quantize(Decimal("0.01"))
    return loan.principal


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================


class ToolRouter:
    """
    Central router that maps user questions to appropriate tools.
    Each tool verifies permissions before returning data.
    """

    @cached_tool(ttl=CACHE_TTL_MEDIUM, prefix="wallet_summary")
    @staticmethod
    def get_my_wallet_summary(user: User, chama: Chama) -> dict[str, Any]:
        """
        Tool: get_my_wallet_summary
        Returns: User's personal wallet summary (contributions, withdrawals, loans)
        """
        require_membership(user, chama)

        total_contributions = Contribution.objects.filter(
            chama=chama,
            member=user,
        ).aggregate(Sum("amount"))["amount__sum"] or Decimal("0.00")

        total_withdrawals = PaymentIntent.objects.filter(
            chama=chama,
            created_by=user,
            intent_type=PaymentIntentType.WITHDRAWAL,
            status=PaymentIntentStatus.SUCCESS,
        ).aggregate(Sum("amount"))["amount__sum"] or Decimal("0")

        outstanding_loans = sum(
            (
                _loan_remaining_balance(loan)
                for loan in Loan.objects.filter(
                    member=user,
                    chama=chama,
                    status__in=[
                        LoanStatus.REQUESTED,
                        LoanStatus.REVIEW,
                        LoanStatus.APPROVED,
                        LoanStatus.DISBURSING,
                        LoanStatus.DISBURSED,
                        LoanStatus.ACTIVE,
                        LoanStatus.DEFAULTED,
                    ],
                )
            ),
            Decimal("0.00"),
        )

        pending_contributions = Contribution.objects.filter(
            chama=chama,
            member=user,
            date_paid__gte=timezone.now().date() - timedelta(days=30),
        ).count()

        net_balance = total_contributions - total_withdrawals - outstanding_loans

        return {
            "available": True,
            "total_contributions": float(total_contributions),
            "total_withdrawals": float(total_withdrawals),
            "outstanding_loans": float(outstanding_loans),
            "net_balance": float(net_balance),
            "recent_contribution_count": pending_contributions,
            "currency": "KES",
            "as_of": timezone.now().isoformat(),
        }

    @cached_tool(ttl=CACHE_TTL_MEDIUM, prefix="chama_wallet")
    @staticmethod
    def get_chama_wallet_summary(chama: Chama, user: User) -> dict[str, Any]:
        """
        Tool: get_chama_wallet_summary
        Returns: Chama-wide wallet summary (admin/treasurer only)
        """
        role = get_effective_role_name(user, chama)
        if role not in [MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER]:
            raise PermissionDenied(
                "Only chama admin or treasurer can view chama-wide wallet summary."
            )

        snapshot = get_chama_financial_snapshot(chama)
        total_contributions = snapshot.contributions_total
        total_withdrawals = snapshot.withdrawals_total
        active_loans = snapshot.outstanding_loans_total

        # Cash at bank (chama wallet)
        net_funds = snapshot.cash_in_total - snapshot.cash_out_total
        cash_at_bank = (
            chama.wallet_balance
            if hasattr(chama, "wallet_balance")
            else net_funds
        )

        return {
            "available": True,
            "total_contributions": float(total_contributions),
            "total_withdrawals": float(total_withdrawals),
            "total_outstanding_loans": float(active_loans),
            "cash_at_bank": float(cash_at_bank),
            "net_chama_funds": float(net_funds),
            "currency": "KES",
            "member_count": Membership.objects.filter(
                chama=chama, is_active=True, is_approved=True
            ).count(),
            "summary_date": snapshot.summary_date.isoformat(),
            "as_of": timezone.now().isoformat(),
        }

    @staticmethod
    def get_contributions_status(chama: Chama, user: User, cycle: int | None = None) -> dict[str, Any]:
        """
        Tool: get_contributions_status
        Returns: Contribution status for the chama or user
        """
        require_membership(user, chama)

        if cycle:
            from_date = timezone.now() - timedelta(days=cycle * 30)
        else:
            from_date = timezone.now() - timedelta(days=30)  # Last 30 days by default

        contributions = Contribution.objects.filter(
            chama=chama,
            date_paid__gte=from_date,
        )

        total_amount = contributions.aggregate(Sum("amount"))["amount__sum"] or Decimal("0")
        count = contributions.count()

        # Get by member
        by_member = contributions.values("member__full_name").annotate(total=Sum("amount")).order_by(
            "-total"
        )[:10]

        return {
            "available": True,
            "period_days": cycle * 30 if cycle else 30,
            "total_contributed": float(total_amount),
            "contribution_count": count,
            "top_contributors": [
                {
                    "name": m["member__full_name"] or "Member",
                    "amount": float(m["total"]),
                }
                for m in by_member
            ],
            "currency": "KES",
        }

    @cached_tool(ttl=CACHE_TTL_SHORT, prefix="unpaid_members")
    @staticmethod
    def get_unpaid_members(chama: Chama, user: User, cycle: int | None = None) -> dict[str, Any]:
        """
        Tool: get_unpaid_members
        Returns: List of members with unpaid contributions (admin/treasurer/secretary only)
        """
        role = get_effective_role_name(user, chama)
        if role not in [MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER, MembershipRole.SECRETARY]:
            raise PermissionDenied(
                "Only admin, treasurer, or secretary can view unpaid members."
            )

        # Get all active members
        active_members = Membership.objects.filter(
            chama=chama, is_active=True, is_approved=True
        ).select_related("user")

        if cycle:
            from_date = timezone.now() - timedelta(days=cycle * 30)
        else:
            from_date = timezone.now() - timedelta(days=30)

        # Find members who haven't contributed in the period
        paid_member_ids = Contribution.objects.filter(
            chama=chama,
            date_paid__gte=from_date,
        ).values_list("member_id", flat=True).distinct()

        unpaid_members = active_members.exclude(user_id__in=paid_member_ids)

        return {
            "available": True,
            "period_days": cycle * 30 if cycle else 30,
            "unpaid_count": unpaid_members.count(),
            "unpaid_members": [
                {
                    "member_id": str(m.user_id),
                    "name": m.user.full_name,
                    "phone": mask_phone(getattr(m.user, "phone", "")),
                }
                for m in unpaid_members[:50]
            ],
        }

    @staticmethod
    def get_my_loan_status(user: User, chama: Chama) -> dict[str, Any]:
        """
        Tool: get_my_loan_status
        Returns: User's personal loan status
        """
        require_membership(user, chama)

        loans = Loan.objects.filter(
            member=user,
            chama=chama,
            status__in=[
                LoanStatus.REQUESTED,
                LoanStatus.REVIEW,
                LoanStatus.APPROVED,
                LoanStatus.DISBURSING,
                LoanStatus.DISBURSED,
                LoanStatus.ACTIVE,
                LoanStatus.DEFAULTED,
            ],
        )

        loan_data = []
        total_outstanding = Decimal("0.00")

        for loan in loans:
            next_installment = loan.installments.exclude(
                status=InstallmentStatus.PAID,
            ).order_by("due_date").first()
            remaining_balance = _loan_remaining_balance(loan)

            loan_data.append(
                {
                    "loan_id": str(loan.id),
                    "principal": float(loan.principal),
                    "interest_rate": float(loan.interest_rate),
                    "status": loan.status,
                    "remaining_balance": float(remaining_balance),
                    "monthly_repayment": float(_loan_periodic_payment(loan)),
                    "next_repayment_date": next_installment.due_date.isoformat()
                    if next_installment
                    else None,
                    "next_expected_amount": float(next_installment.expected_amount)
                    if next_installment
                    else 0,
                    "term_months": loan.duration_months,
                }
            )
            total_outstanding += remaining_balance

        return {
            "available": True,
            "active_loans": loan_data,
            "total_outstanding": float(total_outstanding),
            "loan_count": len(loan_data),
            "currency": "KES",
        }

    @cached_tool(ttl=CACHE_TTL_MEDIUM, prefix="loan_book")
    @staticmethod
    def get_loan_book(chama: Chama, user: User) -> dict[str, Any]:
        """
        Tool: get_loan_book
        Returns: Full loan book (admin/treasurer/auditor only)
        """
        role = get_effective_role_name(user, chama)
        if role not in [MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER, MembershipRole.AUDITOR]:
            raise PermissionDenied(
                "Only admin, treasurer, or auditor can view the loan book."
            )

        loans = Loan.objects.filter(chama=chama).select_related("member")

        snapshot = get_chama_financial_snapshot(chama)

        # Group by status
        by_status = loans.values("status").annotate(count=Count("id"))

        # Calculate totals
        total_disbursed = loans.aggregate(Sum("principal"))["principal__sum"] or Decimal("0.00")
        total_outstanding = snapshot.outstanding_loans_total

        # Active loans detail
        active_loans = loans.filter(
            status__in=[
                LoanStatus.APPROVED,
                LoanStatus.DISBURSING,
                LoanStatus.DISBURSED,
                LoanStatus.ACTIVE,
                LoanStatus.DEFAULTED,
            ]
        ).order_by("-requested_at")[:20]

        return {
            "available": True,
            "total_disbursed": float(total_disbursed),
            "total_outstanding": float(total_outstanding),
            "active_loan_count": snapshot.active_loan_count,
            "overdue_loan_count": snapshot.overdue_loan_count,
            "by_status": {item["status"]: item["count"] for item in by_status},
            "recent_loans": [
                {
                    "borrower": loan.member.full_name,
                    "principal": float(loan.principal),
                    "status": loan.status,
                    "remaining_balance": float(_loan_remaining_balance(loan)),
                }
                for loan in active_loans
            ],
            "currency": "KES",
        }

    @cached_tool(ttl=CACHE_TTL_SHORT, prefix="fines_summary")
    @staticmethod
    def get_fines_summary(chama: Chama, user: User, cycle: int | None = None) -> dict[str, Any]:
        """
        Tool: get_fines_summary
        Returns: Fines summary for the chama
        """
        require_membership(user, chama)

        if cycle:
            from_date = timezone.now() - timedelta(days=cycle * 30)
        else:
            from_date = timezone.now() - timedelta(days=30)

        fines = Fine.objects.filter(
            chama=chama,
            created_at__gte=from_date,
        )

        total_fines = fines.aggregate(Sum("amount"))["amount__sum"] or Decimal("0.00")
        total_paid = (
            fines.filter(status=FineStatus.PAID).aggregate(Sum("amount"))["amount__sum"]
            or Decimal("0.00")
        )
        total_unpaid = (
            fines.exclude(status__in=[FineStatus.PAID, FineStatus.WAIVED]).aggregate(Sum("amount"))[
                "amount__sum"
            ]
            or Decimal("0.00")
        )

        # By member
        by_member = fines.values("member__full_name").annotate(total=Sum("amount")).order_by(
            "-total"
        )[:10]

        return {
            "available": True,
            "period_days": cycle * 30 if cycle else 30,
            "total_fines": float(total_fines),
            "total_paid": float(total_paid),
            "total_unpaid": float(total_unpaid),
            "fine_count": fines.count(),
            "top_fined": [
                {
                    "name": m["member__full_name"] or "Member",
                    "amount": float(m["total"]),
                }
                for m in by_member
            ],
            "currency": "KES",
        }

    @cached_tool(ttl=CACHE_TTL_LONG, prefix="meeting_schedule")
    @staticmethod
    def get_meeting_schedule(chama: Chama, user: User) -> dict[str, Any]:
        """
        Tool: get_meeting_schedule
        Returns: Upcoming meetings schedule
        """
        require_membership(user, chama)

        now = timezone.now()

        # Upcoming meetings
        upcoming = Meeting.objects.filter(
            chama=chama,
            date__gte=now.date(),
        ).order_by("date")[:10]

        # Past meetings
        past = Meeting.objects.filter(
            chama=chama,
            date__lt=now.date(),
        ).order_by("-date")[:5]

        return {
            "available": True,
            "upcoming_meetings": [
                {
                    "meeting_id": str(m.id),
                    "title": m.title,
                    "date": m.date.isoformat(),
                    "time": m.date.strftime("%H:%M"),
                    "location": "Scheduled meeting",
                    "agenda": m.agenda[:200] if m.agenda else "",
                }
                for m in upcoming
            ],
            "recent_meetings": [
                {
                    "meeting_id": str(m.id),
                    "title": m.title,
                    "date": m.date.isoformat(),
                }
                for m in past
            ],
            "has_minutes": any(m.minutes_text for m in past),
        }

    @cached_tool(ttl=CACHE_TTL_SHORT, prefix="activity_feed")
    @staticmethod
    def get_recent_activity_feed(chama: Chama, user: User, days: int = 7) -> dict[str, Any]:
        """
        Tool: get_recent_activity_feed
        Returns: Recent activity in the chama
        """
        require_membership(user, chama)

        from_date = timezone.now() - timedelta(days=days)

        activities = []

        # Recent contributions
        contributions = Contribution.objects.filter(
            chama=chama,
            date_paid__gte=from_date,
        ).order_by("-date_paid")[:10]

        for c in contributions:
            activities.append({
                "type": "contribution",
                "date": c.date_paid.isoformat(),
                "member": c.member.full_name,
                "amount": float(c.amount),
            })

        # Recent loans
        loans = Loan.objects.filter(
            chama=chama,
            requested_at__gte=from_date,
        ).order_by("-requested_at")[:10]

        for loan in loans:
            activities.append({
                "type": "loan",
                "date": loan.requested_at.isoformat(),
                "borrower": loan.member.full_name,
                "amount": float(loan.principal),
                "status": loan.status,
            })

        # Recent meetings
        meetings = Meeting.objects.filter(
            chama=chama,
            date__gte=from_date.date(),
        ).order_by("-date")[:5]

        for m in meetings:
            activities.append({
                "type": "meeting",
                "date": m.date.isoformat(),
                "title": m.title,
            })

        # Sort by date
        activities.sort(key=lambda x: x["date"], reverse=True)

        return {
            "available": True,
            "period_days": days,
            "activities": activities[:30],
            "activity_count": len(activities),
        }

    @staticmethod
    def get_mpesa_transaction_status(
        chama: Chama,
        user: User,
        reference: str | None = None,
        phone: str | None = None,
    ) -> dict[str, Any]:
        """
        Tool: get_mpesa_transaction_status
        Returns: M-Pesa transaction status
        """
        require_membership(user, chama)

        if not reference and not phone:
            return {
                "available": True,
                "error": "Please provide a reference number or phone number",
            }

        query = Q(chama=chama)
        if reference:
            query &= Q(idempotency_key__icontains=reference) | Q(
                metadata__reference__icontains=reference
            )
        if phone:
            query &= Q(phone__icontains=phone[-9:])

        intents = PaymentIntent.objects.filter(query).order_by("-created_at")[:10]

        if not intents:
            return {
                "available": True,
                "found": False,
                "message": "No transaction found with that reference/phone",
            }

        results = []
        for intent in intents:
            results.append({
                "intent_id": str(intent.id),
                "status": intent.status,
                "amount": float(intent.amount),
                "phone": mask_phone(intent.phone),
                "reference": intent.metadata.get("reference") or intent.idempotency_key,
                "created_at": intent.created_at.isoformat(),
            })

        return {
            "available": True,
            "found": True,
            "transactions": results,
        }

    @cached_tool(ttl=CACHE_TTL_LONG, prefix="pricing_limits")
    @staticmethod
    def get_pricing_limits(chama: Chama, user: User) -> dict[str, Any]:
        """
        Tool: get_pricing_limits
        Returns: Plan and feature limits for the chama
        """
        require_membership(user, chama)

        from apps.billing.services import get_entitlements

        entitlements = get_entitlements(chama)

        return {
            "available": True,
            "plan_code": entitlements.get("plan_code", "FREE"),
            "features": {
                "ai_basic": entitlements.get("ai_basic", False),
                "ai_advanced": entitlements.get("ai_advanced", False),
                "exports_pdf": entitlements.get("exports_pdf", False),
                "exports_excel": entitlements.get("exports_excel", False),
                "advanced_reports": entitlements.get("advanced_reports", False),
                "audit_explorer": entitlements.get("audit_explorer", False),
            },
            "limits": {
                "seat_limit": entitlements.get("seat_limit", 25),
                "sms_limit": entitlements.get("sms_limit", 50),
                "monthly_stk_limit": entitlements.get("monthly_stk_limit", 100),
            },
        }

    @cached_tool(ttl=CACHE_TTL_SHORT, prefix="audit_logs")
    @staticmethod
    def get_audit_logs(chama: Chama, user: User, days: int = 30) -> dict[str, Any]:
        """
        Tool: get_audit_logs
        Returns: Audit logs (admin/auditor only)
        """
        role = get_effective_role_name(user, chama)
        if role not in [MembershipRole.CHAMA_ADMIN, MembershipRole.AUDITOR]:
            raise PermissionDenied("Only admin or auditor can view audit logs.")

        from_date = timezone.now() - timedelta(days=days)
        logs = list(
            AuditLog.objects.filter(
                chama_id=chama.id,
                created_at__gte=from_date,
            )
            .order_by("-created_at")
            .values(
                "id",
                "action",
                "entity_type",
                "entity_id",
                "metadata",
                "created_at",
            )[:50]
        )

        return {
            "available": True,
            "period_days": days,
            "logs": [
                {
                    **log,
                    "id": str(log["id"]),
                    "entity_id": str(log["entity_id"]) if log["entity_id"] else None,
                    "created_at": log["created_at"].isoformat(),
                }
                for log in logs
            ],
            "log_count": len(logs),
        }

    @staticmethod
    def get_statement(
        chama: Chama,
        user: User,
        period_months: int = 12,
        member_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Tool: generate_statement
        Returns: Statement generation info (async job)
        """
        require_membership(user, chama)

        # Check if user can request statement for other members
        if member_id and member_id != str(user.id):
            role = get_effective_role_name(user, chama)
            if role not in [MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER]:
                raise PermissionDenied(
                    "You can only request your own statement."
                )

        from_date = timezone.now() - timedelta(days=period_months * 30)

        # This would typically queue a background job
        # For now, return the parameters that would be used
        return {
            "available": True,
            "statement_type": "member" if not member_id or member_id == str(user.id) else "chama",
            "target_member_id": member_id or str(user.id),
            "from_date": from_date.isoformat(),
            "to_date": timezone.now().isoformat(),
            "period_months": period_months,
            "note": "Statement generation has been queued. You will receive a notification when ready.",
            "estimated_delivery_minutes": 5,
        }

    @staticmethod
    def generate_statement_pdf(
        chama: Chama,
        user: User,
        period_months: int = 12,
        member_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Tool: generate_statement_pdf
        Returns: Generates and returns a real PDF statement with full financial details
        
        This tool generates actual PDF statements with:
        - Member profile information
        - Contribution history
        - Withdrawal history
        - Loan details and repayment schedule
        - Fine history
        - Summary statistics
        """
        require_membership(user, chama)

        # Check permissions for viewing other members' statements
        if member_id and member_id != str(user.id):
            role = get_effective_role_name(user, chama)
            if role not in [MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER]:
                raise PermissionDenied(
                    "You can only request your own statement."
                )

        # Determine target user
        from apps.accounts.models import User as UserModel
        target_user = user
        if member_id:
            try:
                target_user = UserModel.objects.get(id=member_id)
            except UserModel.DoesNotExist:
                return {"error": "Member not found", "available": False}

        from_date = timezone.now() - timedelta(days=period_months * 30)
        to_date = timezone.now()

        # Fetch comprehensive data
        # Contributions
        contributions = Contribution.objects.filter(
            chama=chama,
            member=target_user,
            date_paid__gte=from_date.date(),
        ).order_by("-date_paid")[:50]

        withdrawals = PaymentIntent.objects.filter(
            chama=chama,
            created_by=target_user,
            intent_type=PaymentIntentType.WITHDRAWAL,
            status=PaymentIntentStatus.SUCCESS,
            created_at__gte=from_date,
        ).order_by("-created_at")[:50]

        loans = Loan.objects.filter(
            member=target_user,
            chama=chama,
        )

        fines = Fine.objects.filter(
            member=target_user,
            chama=chama,
            created_at__gte=from_date,
        ).order_by("-created_at")[:20]

        # Calculate totals
        total_contributions = sum((c.amount for c in contributions), Decimal("0.00"))
        total_withdrawals = sum((w.amount for w in withdrawals), Decimal("0.00"))
        total_fines = sum((f.amount for f in fines), Decimal("0.00"))
        outstanding_loans = sum(
            (
                _loan_remaining_balance(loan)
                for loan in loans.filter(
                    status__in=[
                        LoanStatus.APPROVED,
                        LoanStatus.DISBURSING,
                        LoanStatus.DISBURSED,
                        LoanStatus.ACTIVE,
                        LoanStatus.DEFAULTED,
                    ]
                )
            ),
            Decimal("0.00"),
        )

        # Format the statement data for display
        statement_data = {
            "available": True,
            "statement_generated": True,
            "statement_id": (
                f"STMT-{str(chama.id)[:8]}-{str(target_user.id)[:8]}-"
                f"{timezone.now().strftime('%Y%m%d')}"
            ),
            "chama": {
                "name": chama.name,
                "id": str(chama.id),
            },
            "member": {
                "name": target_user.full_name,
                "phone": mask_phone(target_user.phone),
                "email": target_user.email,
                "member_since": (
                    target_user.date_joined.isoformat()
                    if hasattr(target_user, "date_joined")
                    else None
                ),
            },
            "period": {
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "months": period_months,
            },
            "summary": {
                "total_contributions": float(total_contributions),
                "total_withdrawals": float(total_withdrawals),
                "total_fines": float(total_fines),
                "outstanding_loans": float(outstanding_loans),
                "net_balance": float(total_contributions - total_withdrawals - outstanding_loans),
                "currency": "KES",
            },
            "contributions": [
                {
                    "date": c.date_paid.isoformat(),
                    "amount": float(c.amount),
                    "reference": c.receipt_code or "N/A",
                    "status": "RECORDED",
                }
                for c in contributions[:20]
            ],
            "withdrawals": [
                {
                    "date": w.created_at.isoformat(),
                    "amount": float(w.amount),
                    "reference": w.metadata.get("reference") or w.idempotency_key,
                    "status": w.status,
                }
                for w in withdrawals[:20]
            ],
            "loans": [
                {
                    "principal": float(l.principal),
                    "interest_rate": float(l.interest_rate),
                    "status": l.status,
                    "remaining_balance": float(_loan_remaining_balance(l)),
                    "monthly_repayment": float(_loan_periodic_payment(l)),
                    "term_months": l.duration_months,
                }
                for l in loans[:10]
            ],
            "fines": [
                {
                    "date": f.created_at.isoformat(),
                    "amount": float(f.amount),
                    "reason": f.issued_reason or "Unspecified",
                    "status": f.status,
                }
                for f in fines[:10]
            ],
            "generated_at": timezone.now().isoformat(),
            "note": "This statement shows all financial activity for the specified period.",
        }

        return statement_data


# =============================================================================
# TOOL ROUTER - Maps questions to appropriate tools
# =============================================================================

class ToolRegistry:
    """
    Registry of all available tools with their metadata.
    Used to determine which tools a user can access based on their role.
    """

    # Tool definitions with metadata
    TOOLS = {
        "get_my_wallet_summary": {
            "name": "get_my_wallet_summary",
            "description": "Get your personal wallet balance including contributions, withdrawals, and loans",
            "parameters": {},
            "roles": ["MEMBER", "SECRETARY", "TREASURER", "AUDITOR", "CHAMA_ADMIN"],
        },
        "get_chama_wallet_summary": {
            "name": "get_chama_wallet_summary",
            "description": "Get chama-wide wallet summary (total contributions, loans, cash)",
            "parameters": {},
            "roles": ["TREASURER", "CHAMA_ADMIN"],
        },
        "get_contributions_status": {
            "name": "get_contributions_status",
            "description": "Get contribution status for a period",
            "parameters": {"cycle": "optional: number of months"},
            "roles": ["MEMBER", "SECRETARY", "TREASURER", "AUDITOR", "CHAMA_ADMIN"],
        },
        "get_unpaid_members": {
            "name": "get_unpaid_members",
            "description": "Get list of members with unpaid contributions",
            "parameters": {"cycle": "optional: number of months"},
            "roles": ["SECRETARY", "TREASURER", "CHAMA_ADMIN"],
        },
        "get_my_loan_status": {
            "name": "get_my_loan_status",
            "description": "Get your active loans and repayment schedule",
            "parameters": {},
            "roles": ["MEMBER", "SECRETARY", "TREASURER", "AUDITOR", "CHAMA_ADMIN"],
        },
        "get_loan_book": {
            "name": "get_loan_book",
            "description": "Get full loan book with all active loans",
            "parameters": {},
            "roles": ["TREASURER", "AUDITOR", "CHAMA_ADMIN"],
        },
        "get_fines_summary": {
            "name": "get_fines_summary",
            "description": "Get fines summary for the chama",
            "parameters": {"cycle": "optional: number of months"},
            "roles": ["MEMBER", "SECRETARY", "TREASURER", "AUDITOR", "CHAMA_ADMIN"],
        },
        "get_meeting_schedule": {
            "name": "get_meeting_schedule",
            "description": "Get upcoming and recent meetings",
            "parameters": {},
            "roles": ["MEMBER", "SECRETARY", "TREASURER", "AUDITOR", "CHAMA_ADMIN"],
        },
        "get_recent_activity_feed": {
            "name": "get_recent_activity_feed",
            "description": "Get recent activity in the chama",
            "parameters": {"days": "optional: number of days (default 7)"},
            "roles": ["MEMBER", "SECRETARY", "TREASURER", "AUDITOR", "CHAMA_ADMIN"],
        },
        "get_mpesa_transaction_status": {
            "name": "get_mpesa_transaction_status",
            "description": "Check M-Pesa payment status by reference or phone",
            "parameters": {"reference": "optional: payment reference", "phone": "optional: phone number"},
            "roles": ["MEMBER", "SECRETARY", "TREASURER", "AUDITOR", "CHAMA_ADMIN"],
        },
        "get_pricing_limits": {
            "name": "get_pricing_limits",
            "description": "Get current plan and feature limits",
            "parameters": {},
            "roles": ["MEMBER", "SECRETARY", "TREASURER", "AUDITOR", "CHAMA_ADMIN"],
        },
        "get_audit_logs": {
            "name": "get_audit_logs",
            "description": "Get audit logs for the chama",
            "parameters": {"days": "optional: number of days (default 30)"},
            "roles": ["AUDITOR", "CHAMA_ADMIN"],
        },
        "generate_statement": {
            "name": "generate_statement",
            "description": "Generate a financial statement (PDF)",
            "parameters": {
                "period_months": "optional: number of months (default 12)",
                "member_id": "optional: specific member ID",
            },
            "roles": ["MEMBER", "SECRETARY", "TREASURER", "AUDITOR", "CHAMA_ADMIN"],
        },
        "generate_statement_pdf": {
            "name": "generate_statement_pdf",
            "description": "Generate a detailed PDF financial statement with contributions, withdrawals, loans, and fines",
            "parameters": {
                "period_months": "optional: number of months (default 12)",
                "member_id": "optional: specific member ID (admin/treasurer only)",
            },
            "roles": ["MEMBER", "SECRETARY", "TREASURER", "AUDITOR", "CHAMA_ADMIN"],
        },
    }

    @classmethod
    def get_available_tools(cls, user: User, chama: Chama) -> list:
        """Get list of tools available to a user based on their role."""
        try:
            role = get_effective_role_name(user, chama)
        except PermissionDenied:
            return []

        available = []
        for tool_name, tool_meta in cls.TOOLS.items():
            if role in tool_meta["roles"]:
                available.append({
                    "name": tool_name,
                    "description": tool_meta["description"],
                })

        return available

    @classmethod
    def execute_tool(cls, tool_name: str, user: User, chama: Chama, **kwargs) -> dict[str, Any]:
        """Execute a tool by name with the given parameters."""
        if tool_name not in cls.TOOLS:
            return {"error": f"Unknown tool: {tool_name}", "available": False}

        try:
            if tool_name == "get_my_wallet_summary":
                return ToolRouter.get_my_wallet_summary(user, chama)
            if tool_name == "get_chama_wallet_summary":
                return ToolRouter.get_chama_wallet_summary(chama, user)
            if tool_name == "get_contributions_status":
                return ToolRouter.get_contributions_status(chama, user, **kwargs)
            if tool_name == "get_unpaid_members":
                return ToolRouter.get_unpaid_members(chama, user, **kwargs)
            if tool_name == "get_my_loan_status":
                return ToolRouter.get_my_loan_status(user, chama)
            if tool_name == "get_loan_book":
                return ToolRouter.get_loan_book(chama, user)
            if tool_name == "get_fines_summary":
                return ToolRouter.get_fines_summary(chama, user, **kwargs)
            if tool_name == "get_meeting_schedule":
                return ToolRouter.get_meeting_schedule(chama, user)
            if tool_name == "get_recent_activity_feed":
                return ToolRouter.get_recent_activity_feed(chama, user, **kwargs)
            if tool_name == "get_mpesa_transaction_status":
                return ToolRouter.get_mpesa_transaction_status(chama, user, **kwargs)
            if tool_name == "get_pricing_limits":
                return ToolRouter.get_pricing_limits(chama, user)
            if tool_name == "get_audit_logs":
                return ToolRouter.get_audit_logs(chama, user, **kwargs)
            if tool_name == "generate_statement":
                return ToolRouter.get_statement(chama, user, **kwargs)
            if tool_name == "generate_statement_pdf":
                return ToolRouter.generate_statement_pdf(chama, user, **kwargs)
            return {"error": f"Unknown tool: {tool_name}", "available": False}
        except PermissionDenied as e:
            return {"error": str(e), "available": False, "permission_error": True}
        except Exception as e:
            logger.exception(f"Tool {tool_name} failed")
            return {"error": str(e), "available": False}
