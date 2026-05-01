"""
Fraud Detection Engine

Real-time fraud scoring engine with rule-based evaluation.
Score 0-100 determines action: allow, flag, pin_required, or block.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum

from django.db import models
from django.utils import timezone

from apps.accounts.models import User
from apps.finance.models import Loan, Wallet
from apps.payments.models import PaymentIntent

from .models import AuditLog, SecurityAlert

logger = logging.getLogger(__name__)


class FraudScore(Enum):
    ALLOW = "allow"
    FLAG = "flag"
    PIN_REQUIRED = "pin_required"
    BLOCK = "block"


@dataclass
class FraudDecision:
    score: int
    action: FraudScore
    reason: str
    rule_hits: list[str]
    metadata: dict


@dataclass
class TransactionContext:
    """Context for transaction fraud evaluation."""

    user: User
    chama_id: str
    amount: Decimal
    transaction_type: str
    device_id: str | None = None
    ip_address: str | None = None
    device_fingerprint: str | None = None
    destination_account: str | None = None
    payment_method: str | None = None
    idempotency_key: str | None = None


class FraudEngine:
    """Real-time fraud detection engine."""

    ALLOW_THRESHOLD = 30
    FLAG_THRESHOLD = 60
    PIN_REQUIRED_THRESHOLD = 80
    BLOCK_THRESHOLD = 81

    @staticmethod
    def evaluate_transaction(context: TransactionContext) -> FraudDecision:
        """Evaluate a transaction for fraud risk."""
        score = 0
        rule_hits = []
        metadata = {}

        score, hits, meta = FraudRules.check_velocity(context)
        score += score
        rule_hits.extend(hits)
        metadata.update(meta)

        score, hits, meta = FraudRules.check_amount_anomaly(context)
        score += score
        rule_hits.extend(hits)
        metadata.update(meta)

        score, hits, meta = FraudRules.check_duplicate(context)
        score += score
        rule_hits.extend(hits)
        metadata.update(meta)

        score, hits, meta = FraudRules.check_device_trust(context)
        score += score
        rule_hits.extend(hits)
        metadata.update(meta)

        score, hits, meta = FraudRules.check_location_anomaly(context)
        score += score
        rule_hits.extend(hits)
        metadata.update(meta)

        score, hits, meta = FraudRules.check_time_anomaly(context)
        score += score
        rule_hits.extend(hits)
        metadata.update(meta)

        score, hits, meta = FraudRules.check_destination_change(context)
        score += score
        rule_hits.extend(hits)
        metadata.update(meta)

        score, hits, meta = FraudRules.check_new_device_payout(context)
        score += score
        rule_hits.extend(hits)
        metadata.update(meta)

        final_score = min(100, score)
        action = FraudEngine._score_to_action(final_score)
        reason = f"Fraud score: {final_score}, hits: {', '.join(rule_hits)}"

        if action == FraudScore.BLOCK:
            FraudEngine._create_security_alert(
                user=context.user,
                chama_id=context.chama_id,
                score=final_score,
                rule_hits=rule_hits,
                context=context,
            )

        return FraudDecision(
            score=final_score,
            action=action,
            reason=reason,
            rule_hits=rule_hits,
            metadata=metadata,
        )

    @staticmethod
    def _score_to_action(score: int) -> FraudScore:
        """Map score to action."""
        if score <= FraudEngine.ALLOW_THRESHOLD:
            return FraudScore.ALLOW
        elif score <= FraudEngine.FLAG_THRESHOLD:
            return FraudScore.FLAG
        elif score <= FraudEngine.PIN_REQUIRED_THRESHOLD:
            return FraudScore.PIN_REQUIRED
        else:
            return FraudScore.BLOCK

    @staticmethod
    def _create_security_alert(
        user: User,
        chama_id: str,
        score: int,
        rule_hits: list[str],
        context: TransactionContext,
    ):
        """Create security alert for high-risk transaction."""
        SecurityAlert.objects.create(
            user=user,
            chama_id=chama_id,
            alert_type=SecurityAlert.AlertType.PAYMENT_ANOMALY,
            level=SecurityAlert.Level.CRITICAL,
            title="High-risk transaction blocked",
            message=f"Transaction blocked due to fraud score {score}. Rule hits: {', '.join(rule_hits)}",
            metadata={
                "score": score,
                "rule_hits": rule_hits,
                "amount": str(context.amount),
                "transaction_type": context.transaction_type,
            },
            ip_address=context.ip_address,
            created_by=user,
            updated_by=user,
        )


class FraudRules:
    """Fraud detection rules."""

    VELOCITY_WINDOW_MINUTES = 10
    VELOCITY_THRESHOLD = 5
    AMOUNT_ANOMALY_MULTIPLIER = 3

    @staticmethod
    def check_velocity(context: TransactionContext) -> tuple[int, list[str], dict]:
        """Check transaction velocity."""
        window = timezone.now() - timedelta(minutes=FraudRules.VELOCITY_WINDOW_MINUTES)
        count = PaymentIntent.objects.filter(
            user=context.user,
            chama_id=context.chama_id,
            created_at__gte=window,
            status__in=["INITIATED", "PENDING", "SUCCESS"],
        ).count()

        hits = []
        if count >= FraudRules.VELOCITY_THRESHOLD:
            hits.append(f"velocity_{count}")
        return min(20, count * 4), hits, {"velocity_count": count}

    @staticmethod
    def check_amount_anomaly(context: TransactionContext) -> tuple[int, list[str], dict]:
        """Check amount anomaly."""
        try:
            wallet = Wallet.objects.get(
                owner_type="USER",
                owner_id=context.user.id,
                chama_id=context.chama_id,
            )
            avg_contribution = wallet.total_contributions / max(1, wallet.contribution_count)
            is_anomaly = context.amount > avg_contribution * FraudRules.AMOUNT_ANOMALY_MULTIPLIER
        except Wallet.DoesNotExist:
            is_anomaly = context.amount > Decimal("10000")

        hits = []
        if is_anomaly:
            hits.append("amount_anomaly")
        return 15 if is_anomaly else 0, hits, {"is_amount_anomaly": is_anomaly}

    @staticmethod
    def check_duplicate(context: TransactionContext) -> tuple[int, list[str], dict]:
        """Check for duplicate transaction."""
        if not context.idempotency_key:
            return 0, [], {}

        exists = PaymentIntent.objects.filter(
            user=context.user,
            chama_id=context.chama_id,
            idempotency_key=context.idempotency_key,
        ).exists()

        hits = []
        if exists:
            hits.append("duplicate")
        return 25 if exists else 0, hits, {"is_duplicate": exists}

    @staticmethod
    def check_device_trust(context: TransactionContext) -> tuple[int, list[str], dict]:
        """Check if device is trusted."""
        from .models import TrustedDevice

        if not context.device_fingerprint:
            return 15, ["new_device_no_fingerprint"], {"is_trusted": False}

        is_trusted = TrustedDevice.objects.filter(
            user=context.user,
            fingerprint=context.device_fingerprint,
            is_trusted=True,
        ).exists()

        hits = []
        if not is_trusted:
            hits.append("untrusted_device")
        return 15 if not is_trusted else 0, hits, {"is_trusted": is_trusted}

    @staticmethod
    def check_location_anomaly(context: TransactionContext) -> tuple[int, list[str], dict]:
        """Check for location anomaly."""
        from .models import DeviceLoginAttempt

        if not context.ip_address:
            return 0, [], {}

        recent = DeviceLoginAttempt.objects.filter(
            user=context.user,
            created_at__gte=timezone.now() - timedelta(days=7),
        ).values_list("ip_address", flat=True).distinct()

        hits = []
        if context.ip_address not in recent and recent:
            hits.append("new_location")
        return 20 if hits else 0, hits, {"is_new_location": bool(hits)}

    @staticmethod
    def check_time_anomaly(context: TransactionContext) -> tuple[int, list[str], dict]:
        """Check for unusual transaction time."""
        hour = timezone.now().hour

        is_unusual = hour < 6 or hour > 22
        hits = []
        if is_unusual:
            hits.append("unusual_time")
        return 10 if is_unusual else 0, hits, {"hour": hour, "is_unusual": is_unusual}

    @staticmethod
    def check_destination_change(context: TransactionContext) -> tuple[int, list[str], dict]:
        """Check payout destination change."""
        if not context.destination_account:
            return 0, [], {}

        recent_destinations = list(
            AuditLog.objects.filter(
                actor=context.user,
                action_type__in=[
                    "WITHDRAWAL_REQUESTED",
                    "WITHDRAWAL_CONFIRMED",
                    "LOAN_DISBURSEMENT_REQUESTED",
                    "LOAN_DISBURSED",
                ],
            )
            .exclude(metadata__destination_account__isnull=True)
            .order_by("-created_at")
            .values_list("metadata__destination_account", flat=True)[:5]
        )
        hits = []
        if recent_destinations and context.destination_account not in recent_destinations:
            hits.append("recent_destination_change")
        return 25 if hits else 0, hits, {"is_new_destination": bool(hits)}

    @staticmethod
    def check_new_device_payout(context: TransactionContext) -> tuple[int, list[str], dict]:
        """Block payout from new device immediately after login."""
        from .models import DeviceLoginAttempt

        if not context.device_fingerprint or context.transaction_type != "payout":
            return 0, [], {}

        recent_login = DeviceLoginAttempt.objects.filter(
            user=context.user,
            fingerprint=context.device_fingerprint,
            success=True,
            created_at__gte=timezone.now() - timedelta(minutes=30),
        ).exists()

        hits = []
        if not recent_login:
            hits.append("new_device_payout")
        return 20 if hits else 0, hits, {"is_new_device_payout": bool(hits)}


class LoanFraudRules:
    """Loan-specific fraud detection rules."""

    @staticmethod
    def check_rapid_applications(context: TransactionContext) -> tuple[int, list[str], dict]:
        """Check for rapid loan applications."""
        window = timezone.now() - timedelta(hours=24)
        count = Loan.objects.filter(
            member__user=context.user,
            chama_id=context.chama_id,
            created_at__gte=window,
            status__in=[Loan.LoanStatus.REQUESTED, Loan.LoanStatus.REVIEW],
        ).count()

        hits = []
        if count > 3:
            hits.append("rapid_applications")
        return 20 if hits else 0, hits, {"application_count": count}

    @staticmethod
    def check_contribution_history(context: TransactionContext) -> tuple[int, list[str], dict]:
        """Check contribution history plausibility."""
        try:
            wallet = Wallet.objects.get(
                owner_type="USER",
                owner_id=context.user.id,
                chama_id=context.chama_id,
            )
            months_member = (timezone.now() - wallet.created_at).days / 30
            expected_contributions = wallet.contribution_count / max(1, months_member)

            is_implausible = expected_contributions < 1
            hits = []
            if is_implausible:
                hits.append("low_contribution_history")
            return 15 if is_implausible else 0, hits, {}
        except Wallet.DoesNotExist:
            return 20, ["no_contribution_history"], {}

    @staticmethod
    def check_guarantor_overlap(context: TransactionContext, guarantor_ids: list) -> tuple[int, list[str], dict]:
        """Check for guarantor overlap / collusion."""
        from apps.chama.models import Membership

        hits = []
        for gid in guarantor_ids:
            other_loans = Loan.objects.filter(
                member__user_id=gid,
                status__in=[Loan.LoanStatus.ACTIVE, Loan.LoanStatus.DISBURSED],
            ).count()

            if other_loans > 2:
                hits.append("guarantor_overlap")

        return 25 if hits else 0, hits, {}


class AMLRules:
    """Anti-Money Laundering detection rules."""

    CURRENCY_KES = "KES"
    STRUCTURING_THRESHOLD = Decimal("750000")
    CASH_TRANSACTION_LIMIT = Decimal("1000000")

    @staticmethod
    def check_structuring(context: TransactionContext) -> tuple[bool, str]:
        """Check for structuring (multiple small transactions to avoid reporting)."""
        window = timezone.now() - timedelta(days=1)
        total = PaymentIntent.objects.filter(
            user=context.user,
            chama_id=context.chama_id,
            created_at__gte=window,
            status="SUCCESS",
        ).aggregate(total=models.Sum("amount"))["total"] or 0

        if total >= AMLRules.STRUCTURING_THRESHOLD:
            return True, "structuring_detected"
        return False, ""

    @staticmethod
    def check_large_cash_transaction(context: TransactionContext) -> tuple[bool, str]:
        """Check for large cash transaction."""
        if (
            context.amount >= AMLRules.CASH_TRANSACTION_LIMIT
            and context.payment_method == "cash"
        ):
            return True, "large_cash_transaction"
        return False, ""


__all__ = [
    "FraudEngine",
    "FraudRules",
    "LoanFraudRules",
    "AMLRules",
    "FraudDecision",
    "FraudScore",
    "TransactionContext",
]
