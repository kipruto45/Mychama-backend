"""
Fraud Detection Engine for Digital Chama

Rules-based fraud detection:
- Rapid withdrawal after large deposit
- Multiple STK push failures
- Unusual loan patterns
- Device/token changes
- Unusual transaction amounts
"""

from datetime import timedelta
from decimal import Decimal
from typing import Optional

from django.db.models import Count, Q
from django.utils import timezone

from apps.ai.models import FraudFlag, FraudSeverity, FraudType
from apps.finance.models import LedgerEntry
from apps.payments.models import PaymentIntent as Payment


class FraudEngine:
    """
    Rules-based fraud detection engine.
    """
    
    # Thresholds
    RAPID_WITHDRAWAL_THRESHOLD_HOURS = 24
    RAPID_WITHDRAWAL_RATIO = 0.8  # 80% withdrawal triggers flag
    STK_FAILURE_THRESHOLD = 3
    STK_FAILURE_WINDOW_HOURS = 1
    UNUSUAL_AMOUNT_MULTIPLIER = 3  # 3x average = unusual
    
    @classmethod
    def check_all_rules(
        cls,
        user,
        chama,
        trigger_rule: Optional[str] = None,
    ) -> list[FraudFlag]:
        """
        Run all fraud detection rules or specific rule.
        """
        flags = []
        
        rules = [
            cls.check_rapid_withdrawal,
            cls.check_stk_failures,
            cls.check_unusual_loan_pattern,
            cls.check_device_changes,
            cls.check_unusual_amount,
        ]
        
        for rule in rules:
            if trigger_rule and rule.__name__ != trigger_rule:
                continue
            
            try:
                flag = rule(user, chama)
                if flag:
                    flags.append(flag)
            except Exception as e:
                # Log error but don't break other checks
                print(f"Error in {rule.__name__}: {e}")
        
        return flags
    
    @classmethod
    def check_rapid_withdrawal(
        cls,
        user,
        chama,
    ) -> Optional[FraudFlag]:
        """
        Flag if user withdraws >80% of contributions within 24 hours.
        """
        now = timezone.now()
        window_start = now - timedelta(hours=cls.RAPID_WITHDRAWAL_THRESHOLD_HOURS)
        
        # Get deposits in window
        deposits = LedgerEntry.objects.filter(
            owner=user,
            chama=chama,
            entry_type=LedgerEntry.ENTRY_CONTRIBUTION,
            status=LedgerEntry.STATUS_SUCCESS,
            created_at__gte=window_start,
        ).aggregate(total=Count("id"), sum_amount=Sum("amount"))
        
        total_deposits = deposits["sum_amount"] or Decimal("0")
        if total_deposits == 0:
            return None
        
        # Get withdrawals in window
        withdrawals = LedgerEntry.objects.filter(
            owner=user,
            chama=chama,
            entry_type=LedgerEntry.ENTRY_WITHDRAWAL,
            status=LedgerEntry.STATUS_SUCCESS,
            created_at__gte=window_start,
        ).aggregate(total=Count("id"), sum_amount=Sum("amount"))
        
        total_withdrawals = withdrawals["sum_amount"] or Decimal("0")
        
        # Check if ratio exceeds threshold
        withdrawal_ratio = total_withdrawals / total_deposits
        if withdrawal_ratio >= cls.RAPID_WITHDRAWAL_RATIO:
            # Check if already flagged
            existing = FraudFlag.objects.filter(
                user=user,
                chama=chama,
                fraud_type=FraudType.RAPID_WITHDRAWAL,
                resolved=False,
                created_at__gte=window_start,
            ).exists()
            
            if existing:
                return None
            
            # Create flag
            severity = (
                FraudSeverity.CRITICAL if withdrawal_ratio >= Decimal("1.0")
                else FraudSeverity.HIGH if withdrawal_ratio >= Decimal("0.9")
                else FraudSeverity.MEDIUM
            )
            
            return FraudFlag.objects.create(
                user=user,
                chama=chama,
                fraud_type=FraudType.RAPID_WITHDRAWAL,
                severity=severity,
                description=f"User withdrew {withdrawal_ratio:.0%} of recent deposits within {cls.RAPID_WITHDRAWAL_THRESHOLD_HOURS}h",
                evidence={
                    "total_deposits": str(total_deposits),
                    "total_withdrawals": str(total_withdrawals),
                    "ratio": float(withdrawal_ratio),
                    "window_hours": cls.RAPID_WITHDRAWAL_THRESHOLD_HOURS,
                },
            )
        
        return None
    
    @classmethod
    def check_stk_failures(
        cls,
        user,
        chama,
    ) -> Optional[FraudFlag]:
        """
        Flag if user has >3 STK push failures in 1 hour.
        """
        now = timezone.now()
        window_start = now - timedelta(hours=cls.STK_FAILURE_WINDOW_HOURS)
        
        # Get failed payments
        failed_payments = Payment.objects.filter(
            chama=chama,
            created_at__gte=window_start,
            status__in=[Payment.STATUS_FAILED, Payment.STATUS_CANCELLED],
        )
        
        # Check if user caused these
        user_failures = failed_payments.filter(
            Q(payer=user) | Q(phone_number=user.phone_number)
        ).count()
        
        if user_failures >= cls.STK_FAILURE_THRESHOLD:
            # Check if already flagged
            existing = FraudFlag.objects.filter(
                user=user,
                chama=chama,
                fraud_type=FraudType.STK_FAILURES,
                resolved=False,
                created_at__gte=window_start,
            ).exists()
            
            if existing:
                return None
            
            return FraudFlag.objects.create(
                user=user,
                chama=chama,
                fraud_type=FraudType.STK_FAILURES,
                severity=FraudSeverity.MEDIUM,
                description=f"User has {user_failures} failed payment attempts in {cls.STK_FAILURE_WINDOW_HOURS} hour(s)",
                evidence={
                    "failure_count": user_failures,
                    "window_hours": cls.STK_FAILURE_WINDOW_HOURS,
                },
            )
        
        return None
    
    @classmethod
    def check_unusual_loan_pattern(
        cls,
        user,
        chama,
    ) -> Optional[FraudFlag]:
        """
        Flag if user applies for loans immediately after joining
        or has multiple pending loan applications.
        """
        from apps.chama.models import Membership
        from apps.finance.models import Loan
        
        # Get membership
        try:
            membership = Membership.objects.get(user=user, chama=chama)
        except Membership.DoesNotExist:
            return None
        
        # Check if joined recently
        if membership.join_date:
            days_since_join = (timezone.now().date() - membership.join_date).days
            if days_since_join < 7:
                # Check for loan applications
                recent_loans = Loan.objects.filter(
                    borrower=user,
                    chama=chama,
                    created_at__gte=membership.join_date,
                ).count()
                
                if recent_loans > 0:
                    existing = FraudFlag.objects.filter(
                        user=user,
                        chama=chama,
                        fraud_type=FraudType.UNUSUAL_LOAN_PATTERN,
                        resolved=False,
                    ).exists()
                    
                    if not existing:
                        return FraudFlag.objects.create(
                            user=user,
                            chama=chama,
                            fraud_type=FraudType.UNUSUAL_LOAN_PATTERN,
                            severity=FraudSeverity.LOW,
                            description=f"User applied for loan within {days_since_join} days of joining",
                            evidence={
                                "days_since_join": days_since_join,
                                "loan_count": recent_loans,
                            },
                        )
        
        # Check for multiple pending applications
        pending_loans = Loan.objects.filter(
            borrower=user,
            chama=chama,
            status__in=[Loan.STATUS_PENDING, Loan.STATUS_APPROVED],
        ).count()
        
        if pending_loans >= 3:
            existing = FraudFlag.objects.filter(
                user=user,
                chama=chama,
                fraud_type=FraudType.UNUSUAL_LOAN_PATTERN,
                resolved=False,
            ).exists()
            
            if not existing:
                return FraudFlag.objects.create(
                    user=user,
                    chama=chama,
                    fraud_type=FraudType.UNUSUAL_LOAN_PATTERN,
                    severity=FraudSeverity.MEDIUM,
                    description=f"User has {pending_loans} pending/approved loans",
                    evidence={"pending_loans": pending_loans},
                )
        
        return None
    
    @classmethod
    def check_device_changes(
        cls,
        user,
        chama,
    ) -> Optional[FraudFlag]:
        """
        Flag if user's device token changes multiple times in short period.
        """
        # This would require device tracking - placeholder implementation
        # In production, you'd track FCM tokens and device IDs
        
        # For now, return None as we need device tracking infrastructure
        return None
    
    @classmethod
    def check_unusual_amount(
        cls,
        user,
        chama,
    ) -> Optional[FraudFlag]:
        """
        Flag if transaction amount is >3x user's average.
        """
        # Get user's average contribution
        avg_contribution = LedgerEntry.objects.filter(
            owner=user,
            chama=chama,
            entry_type=LedgerEntry.ENTRY_CONTRIBUTION,
            status=LedgerEntry.STATUS_SUCCESS,
        ).aggregate(avg=Avg("amount"))["avg"]
        
        if not avg_contribution or avg_contribution == 0:
            return None
        
        # Get recent transactions (last 24 hours)
        recent = LedgerEntry.objects.filter(
            owner=user,
            chama=chama,
            status=LedgerEntry.STATUS_SUCCESS,
            created_at__gte=timezone.now() - timedelta(hours=24),
        )
        
        for entry in recent:
            if entry.amount > avg_contribution * cls.UNUSUAL_AMOUNT_MULTIPLIER:
                existing = FraudFlag.objects.filter(
                    user=user,
                    chama=chama,
                    fraud_type=FraudType.UNUSUAL_AMOUNT,
                    resolved=False,
                    created_at__gte=timezone.now() - timedelta(hours=24),
                ).exists()
                
                if not existing:
                    return FraudFlag.objects.create(
                        user=user,
                        chama=chama,
                        fraud_type=FraudType.UNUSUAL_AMOUNT,
                        severity=FraudSeverity.LOW,
                        description=f"Unusual transaction amount: KES {entry.amount} (avg: KES {avg_contribution})",
                        evidence={
                            "transaction_amount": str(entry.amount),
                            "average_amount": str(avg_contribution),
                            "multiplier": float(entry.amount / avg_contribution),
                        },
                    )
        
        return None
    
    @classmethod
    def resolve_flag(
        cls,
        flag_id: int,
        resolved_by,
        resolution_note: str,
    ) -> FraudFlag:
        """
        Resolve a fraud flag.
        """
        flag = FraudFlag.objects.get(id=flag_id)
        flag.resolved = True
        flag.resolved_by = resolved_by
        flag.resolution_note = resolution_note
        flag.save()
        
        # Optionally notify user
        # send_fraud_resolved_notification(flag)
        
        return flag


# Import missing aggregates
from django.db.models import Sum, Avg
