"""
Production-Grade Fraud Detection Engine

Real-time fraud scoring with:
- Rule-based detection
- Transaction scoring
- Account takeover detection
- Loan fraud rules
- AML controls
- Case management
- Investigation workflows
"""

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from enum import IntEnum
from typing import Any

from django.conf import settings
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounts.models import OTPToken, PasswordResetToken, User
from apps.finance.models import Loan, Wallet
from apps.payments.models import PaymentIntent
from apps.security.models import AuditLog, LoginAttempt, TrustedDevice

logger = logging.getLogger(__name__)


class FraudScore(IntEnum):
    """Fraud score action tiers."""
    ALLOW = 0
    FLAG = 1
    STEP_UP = 2
    BLOCK = 3


class FraudSeverity(IntEnum):
    """Fraud severity levels."""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class FraudEventType(models.TextChoices):
    """Types of fraud-relevant events."""
    LOGIN = "login", "Login"
    LOGIN_FAILED = "login_failed", "Failed Login"
    PASSWORD_RESET = "password_reset", "Password Reset"
    PHONE_CHANGE = "phone_change", "Phone Number Change"
    KYC_SUBMISSION = "kyc_submission", "KYC Submission"
    CONTRIBUTION = "contribution", "Contribution"
    WALLET_DEPOSIT = "wallet_deposit", "Wallet Deposit"
    WITHDRAWAL = "withdrawal", "Withdrawal"
    LOAN_APPLICATION = "loan_application", "Loan Application"
    LOAN_APPROVAL = "loan_approval", "Loan Approval"
    LOAN_DISBURSEMENT = "loan_disbursement", "Loan Disbursement"
    DISBURSEMENT_DESTINATION_CHANGE = "disbursement_dest_change", "Destination Change"
    DEVICE_NEW = "device_new", "New Device"
    OTP_ABUSE = "otp_abuse", "OTP Abuse"
    SESSION_REUSE = "session_reuse", "Session Reuse"


class FraudCaseStatus(models.TextChoices):
    """Fraud case status."""
    OPEN = "open", "Open"
    INVESTIGATING = "investigating", "Investigating"
    ESCALATED = "escalated", "Escalated"
    RESOLVED_TRUE = "resolved_true_positive", "True Positive"
    RESOLVED_FALSE = "resolved_false_positive", "False Positive"
    FROZEN = "frozen", "Account Frozen"
    RELEASED = "released", "Released"


@dataclass
class FraudSignal:
    """Individual fraud signal."""
    rule_name: str
    score: int
    reason: str
    severity: int
    metadata: dict = field(default_factory=dict)


@dataclass
class FraudEvaluation:
    """Complete fraud evaluation result."""
    score: int
    action: FraudScore
    severity: FraudSeverity
    signals: list[FraudSignal]
    reasons: list[str]
    decision_time_ms: int
    metadata: dict = field(default_factory=dict)


class FraudEngine:
    """
    Production Fraud Detection Engine.
    
    Scores 0-100:
    - 0-30: Allow + log
    - 31-60: Allow + flag for review
    - 61-80: Require step-up verification
    - 81-100: Block + freeze + alert
    """

    ALLOW_MAX = 30
    FLAG_MAX = 60
    STEP_UP_MAX = 80
    BLOCK_MIN = 81

    @staticmethod
    def evaluate_event(
        user: User,
        event_type: str,
        context: dict = None,
    ) -> FraudEvaluation:
        """Evaluate a fraud-relevant event."""
        context = context or {}
        signals = []
        metadata = {"event_type": event_type, "user_id": str(user.id)}
        
        total_score = 0
        
        if event_type == FraudEventType.LOGIN:
            sigs = FraudRules.check_login_context(user, context)
            signals.extend(sigs)
            for s in sigs:
                total_score += s.score
            
            sigs = FraudRules.check_new_device(user, context)
            signals.extend(sigs)
            for s in sigs:
                total_score += s.score
            
            sigs = FraudRules.check_impossible_travel(user, context)
            signals.extend(sigs)
            for s in sigs:
                total_score += s.score
        
        elif event_type == FraudEventType.WITHDRAWAL:
            sigs = FraudRules.check_withdrawal_context(user, context)
            signals.extend(sigs)
            for s in sigs:
                total_score += s.score
            
            sigs = FraudRules.check_velocity(user, context)
            signals.extend(sigs)
            for s in sigs:
                total_score += s.score
            
            sigs = FraudRules.check_destination_change(user, context)
            signals.extend(sigs)
            for s in sigs:
                total_score += s.score
        
        elif event_type == FraudEventType.LOAN_APPLICATION:
            sigs = FraudRules.check_loan_application(user, context)
            signals.extend(sigs)
            for s in sigs:
                total_score += s.score
            
            sigs = FraudRules.check_contribution_ratio(user, context)
            signals.extend(sigs)
            for s in sigs:
                total_score += s.score
        
        elif event_type == FraudEventType.LOAN_DISBURSEMENT:
            sigs = FraudRules.check_disbursement_context(user, context)
            signals.extend(sigs)
            for s in sigs:
                total_score += s.score
        
        elif event_type == FraudEventType.DISBURSEMENT_DESTINATION_CHANGE:
            sigs = FraudRules.check_destination_change(user, context)
            signals.extend(sigs)
            for s in sigs:
                total_score += s.score
        
        elif event_type == FraudEventType.PASSWORD_RESET:
            sigs = FraudRules.check_password_reset_pattern(user, context)
            signals.extend(sigs)
            for s in sigs:
                total_score += s.score
        
        elif event_type == FraudEventType.OTP_ABUSE:
            sigs = FraudRules.check_otp_abuse(user, context)
            signals.extend(sigs)
            for s in sigs:
                total_score += s.score
        
        else:
            sigs = FraudRules.check_generic_context(user, context)
            signals.extend(sigs)
            for s in sigs:
                total_score += s.score
        
        final_score = min(100, total_score)
        action = FraudEngine._score_to_action(final_score)
        severity = FraudEngine._score_to_severity(final_score)
        reasons = [s.reason for s in signals]
        
        return FraudEvaluation(
            score=final_score,
            action=action,
            severity=severity,
            signals=signals,
            reasons=reasons,
            decision_time_ms=0,
            metadata=metadata,
        )

    @staticmethod
    def evaluate_transaction(
        user: User,
        transaction_type: str,
        amount: Decimal,
        chama_id: str,
        context: dict = None,
    ) -> FraudEvaluation:
        """Evaluate a transaction."""
        context = context or {}
        context.update({
            "transaction_type": transaction_type,
            "amount": amount,
            "chama_id": chama_id,
        })
        
        eval_result = FraudEngine.evaluate_event(user, transaction_type, context)
        return eval_result

    @staticmethod
    def _score_to_action(score: int) -> FraudScore:
        if score <= FraudEngine.ALLOW_MAX:
            return FraudScore.ALLOW
        elif score <= FraudEngine.FLAG_MAX:
            return FraudScore.FLAG
        elif score <= FraudEngine.STEP_UP_MAX:
            return FraudScore.STEP_UP
        else:
            return FraudScore.BLOCK

    @staticmethod
    def _score_to_severity(score: int) -> FraudSeverity:
        if score <= FraudEngine.ALLOW_MAX:
            return FraudSeverity.LOW
        elif score <= FraudEngine.FLAG_MAX:
            return FraudSeverity.MEDIUM
        elif score <= FraudEngine.STEP_UP_MAX:
            return FraudSeverity.HIGH
        else:
            return FraudSeverity.CRITICAL

    @staticmethod
    def get_action_description(action: FraudScore) -> str:
        descriptions = {
            FraudScore.ALLOW: "Allow and log",
            FraudScore.FLAG: "Allow and flag for review",
            FraudScore.STEP_UP: "Require step-up verification",
            FraudScore.BLOCK: "Block and freeze account",
        }
        return descriptions.get(action, "Unknown")


class FraudRules:
    """Collection of fraud detection rules."""

    @staticmethod
    def check_login_context(user: User, context: dict) -> list[FraudSignal]:
        """Check login context for anomaly."""
        signals = []
        
        failed_logins = LoginAttempt.objects.filter(
            user=user,
            success=False,
            created_at__gte=timezone.now() - timedelta(minutes=30),
        ).count()
        
        if failed_logins >= 5:
            signals.append(FraudSignal(
                rule_name="failed_login_velocity",
                score=20,
                reason="5+ failed logins in 30 minutes",
                severity=FraudSeverity.MEDIUM.value,
            ))
        
        return signals

    @staticmethod
    def check_new_device(user: User, context: dict) -> list[FraudSignal]:
        """Check for new device usage."""
        signals = []
        
        device_fingerprint = context.get("device_fingerprint")
        if not device_fingerprint:
            return signals
        
        is_known = TrustedDevice.objects.filter(
            user=user,
            fingerprint=device_fingerprint,
            is_trusted=True,
        ).exists()
        
        if not is_known:
            signals.append(FraudSignal(
                rule_name="new_device",
                score=15,
                reason="Login from unrecognized device",
                severity=FraudSeverity.MEDIUM.value,
            ))
        
        return signals

    @staticmethod
    def check_impossible_travel(user: User, context: dict) -> list[FraudSignal]:
        """Check for impossible travel."""
        signals = []
        
        last_login = LoginAttempt.objects.filter(
            user=user,
            success=True,
        ).order_by("-created_at").first()
        
        current_login = context.get("login_time")
        if not last_login or not current_login:
            return signals
        
        time_diff = (current_login - last_login.created_at).total_seconds() / 3600
        ip_change = last_login.ip_address != context.get("ip_address")
        
        if ip_change and time_diff < 1:
            signals.append(FraudSignal(
                rule_name="impossible_travel",
                score=30,
                reason="Impossible travel detected",
                severity=FraudSeverity.HIGH.value,
            ))
        
        return signals

    @staticmethod
    def check_withdrawal_context(user: User, context: dict) -> list[FraudSignal]:
        """Check withdrawal context."""
        signals = []
        
        amount = context.get("amount", Decimal("0"))
        chama_id = context.get("chama_id")
        
        if not chama_id:
            return signals
        
        try:
            wallet = Wallet.objects.get(
                owner_type="USER",
                owner_id=user.id,
                chama_id=chama_id,
            )
            avg_withdrawal = wallet.total_withdrawals / max(1, wallet.withdrawal_count)
            
            if amount > avg_withdrawal * 3 and amount > 10000:
                signals.append(FraudSignal(
                    rule_name="amount_anomaly",
                    score=20,
                    reason="Withdrawal amount unusual",
                    severity=FraudSeverity.MEDIUM.value,
                ))
        except Wallet.DoesNotExist:
            pass
        
        return signals

    @staticmethod
    def check_velocity(user: User, context: dict) -> list[FraudSignal]:
        """Check transaction velocity."""
        signals = []
        
        chama_id = context.get("chama_id")
        transaction_type = context.get("transaction_type")
        
        if not chama_id or not transaction_type:
            return signals
        
        window = timedelta(hours=1)
        count = PaymentIntent.objects.filter(
            user=user,
            chama_id=chama_id,
            intent_type=transaction_type.upper(),
            created_at__gte=timezone.now() - window,
            status__in=["INITIATED", "PENDING", "SUCCESS"],
        ).count()
        
        if count >= 5:
            signals.append(FraudSignal(
                rule_name="velocity_limit",
                score=25,
                reason="High transaction velocity",
                severity=FraudSeverity.HIGH.value,
            ))
        
        return signals

    @staticmethod
    def check_destination_change(user: User, context: dict) -> list[FraudSignal]:
        """Check payout destination change risk."""
        signals = []
        
        chama_id = context.get("chama_id")
        new_destination = context.get("destination_account")
        
        if not chama_id or not new_destination:
            return signals
        
        recent_destination = (
            AuditLog.objects.filter(
                actor=user,
                action_type__in=[
                    "WITHDRAWAL_REQUESTED",
                    "WITHDRAWAL_CONFIRMED",
                    "LOAN_DISBURSEMENT_REQUESTED",
                    "LOAN_DISBURSED",
                ],
            )
            .exclude(metadata__destination_account__isnull=True)
            .order_by("-created_at")
            .values_list("metadata__destination_account", flat=True)
            .first()
        )

        if recent_destination and recent_destination != new_destination:
            signals.append(FraudSignal(
                rule_name="recent_destination_change",
                score=25,
                reason="Payout destination changed from the last recorded destination",
                severity=FraudSeverity.HIGH.value,
            ))
        
        return signals

    @staticmethod
    def check_loan_application(user: User, context: dict) -> list[FraudSignal]:
        """Check loan application for fraud."""
        signals = []
        
        chama_id = context.get("chama_id")
        
        if not chama_id:
            return signals
        
        recent = Loan.objects.filter(
            member__user=user,
            chama_id=chama_id,
            created_at__gte=timezone.now() - timedelta(hours=24),
            status__in=[Loan.LoanStatus.REQUESTED, Loan.LoanStatus.REVIEW],
        ).count()
        
        if recent >= 3:
            signals.append(FraudSignal(
                rule_name="rapid_applications",
                score=20,
                reason="Multiple loan applications in 24 hours",
                severity=FraudSeverity.HIGH.value,
            ))
        
        return signals

    @staticmethod
    def check_contribution_ratio(user: User, context: dict) -> list[FraudSignal]:
        """Check contribution-to-loan ratio."""
        signals = []
        
        chama_id = context.get("chama_id")
        requested_amount = context.get("amount", Decimal("0"))
        
        if not chama_id or not requested_amount:
            return signals
        
        try:
            wallet = Wallet.objects.get(
                owner_type="USER",
                owner_id=user.id,
                chama_id=chama_id,
            )
            
            if wallet.total_contributions > 0:
                ratio = requested_amount / wallet.total_contributions
                if ratio > 3:
                    signals.append(FraudSignal(
                        rule_name="loan_ratio_exceeded",
                        score=20,
                        reason="Loan exceeds contribution ratio limit",
                        severity=FraudSeverity.HIGH.value,
                    ))
        except Wallet.DoesNotExist:
            pass
        
        return signals

    @staticmethod
    def check_disbursement_context(user: User, context: dict) -> list[FraudSignal]:
        """Check disbursement context."""
        signals = []
        
        kyc_tier = context.get("kyc_tier", "tier_0")
        if kyc_tier in ["tier_0", "tier_1"]:
            signals.append(FraudSignal(
                rule_name="insufficient_kyc",
                score=30,
                reason="Insufficient KYC for disbursement",
                severity=FraudSeverity.HIGH.value,
            ))
        
        return signals

    @staticmethod
    def check_password_reset_pattern(user: User, context: dict) -> list[FraudSignal]:
        """Check password reset patterns."""
        signals = []
        
        resets = PasswordResetToken.objects.filter(
            user=user,
            created_at__gte=timezone.now() - timedelta(hours=24),
        ).count()
        
        if resets >= 3:
            signals.append(FraudSignal(
                rule_name="rapid_password_resets",
                score=25,
                reason="Multiple password resets",
                severity=FraudSeverity.HIGH.value,
            ))
        
        return signals

    @staticmethod
    def check_otp_abuse(user: User, context: dict) -> list[FraudSignal]:
        """Check for OTP abuse."""
        signals = []
        
        otp_count = OTPToken.objects.filter(
            user=user,
            created_at__gte=timezone.now() - timedelta(hours=1),
        ).count()
        
        if otp_count >= 10:
            signals.append(FraudSignal(
                rule_name="otp_abuse",
                score=30,
                reason="OTP request abuse",
                severity=FraudSeverity.CRITICAL.value,
            ))
        
        return signals

    @staticmethod
    def check_generic_context(user: User, context: dict) -> list[FraudSignal]:
        """Generic context checks."""
        signals = []
        
        ip_address = context.get("ip_address")
        if ip_address:
            
            if ip_address in settings.BLOCKED_IPS:
                signals.append(FraudSignal(
                    rule_name="blocked_ip",
                    score=50,
                    reason="Request from blocked IP",
                    severity=FraudSeverity.CRITICAL.value,
                ))
        
        return signals


class AMLRules:
    """Anti-Money Laundering detection rules."""

    CASH_TRANSACTION_LIMIT = Decimal("1000000")
    STRUCTURING_THRESHOLD = Decimal("750000")

    @staticmethod
    def check_structuring(user: User, chama_id: str) -> tuple[bool, str]:
        """Check for structuring (multiple small transactions)."""
        window = timezone.now() - timedelta(days=1)
        
        total = PaymentIntent.objects.filter(
            user=user,
            chama_id=chama_id,
            created_at__gte=window,
            status="SUCCESS",
        ).aggregate(total=Sum("amount"))["total"] or 0
        
        if total >= AMLRules.STRUCTURING_THRESHOLD:
            return True, "Potential structuring detected"
        
        return False, ""

    @staticmethod
    def check_large_cash_transaction(amount: Decimal, method: str) -> tuple[bool, str]:
        """Check for large cash transaction."""
        if amount >= AMLRules.CASH_TRANSACTION_LIMIT and method == "cash":
            return True, "Large cash transaction"
        return False, ""


class FraudCase:
    """Fraud case management."""

    @staticmethod
    @transaction.atomic
    def create_case(
        user: User,
        case_type: str,
        evaluation: FraudEvaluation,
        context: dict = None,
    ) -> "FraudCase":
        """Create fraud case from evaluation."""
        from .models import FraudCase

        case = FraudCase.objects.create(
            user=user,
            case_type=case_type,
            severity=evaluation.severity,
            status=FraudCaseStatus.OPEN,
            fraud_score=evaluation.score,
            triggered_by=str(evaluation.signals),
            metadata=context or {},
        )
        
        logger.warning(f"Fraud case created: {case.id} for user {user.id}, score: {evaluation.score}")
        
        if evaluation.action == FraudScore.BLOCK:
            FraudCase.freeze_account(user, case)
        
        return case

    @staticmethod
    @transaction.atomic
    def freeze_account(user: User, case: "FraudCase"):
        """Freeze user account."""
        user.is_active = False
        user.save(update_fields=["is_active"])
        
        case.status = FraudCaseStatus.FROZEN
        case.frozen_at = timezone.now()
        case.save()
        
        logger.warning(f"Account frozen: user {user.id}, case: {case.id}")

    @staticmethod
    @transaction.atomic
    def unfreeze_account(user: User, case: "FraudCase", reviewer: User, notes: str):
        """Unfreeze account."""
        user.is_active = True
        user.save(update_fields=["is_active"])
        
        case.status = FraudCaseStatus.RELEASED
        case.reviewed_by = reviewer
        case.review_note = notes
        case.resolved_at = timezone.now()
        case.save()
        
        logger.info(f"Account unfrozen: user {user.id}, case: {case.id}, by: {reviewer.id}")

__all__ = [
    "FraudScore",
    "FraudSeverity",
    "FraudEventType",
    "FraudCaseStatus",
    "FraudSignal",
    "FraudEvaluation",
    "FraudEngine",
    "FraudRules",
    "AMLRules",
    "FraudCase",
]
