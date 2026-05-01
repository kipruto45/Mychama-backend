"""
Immutable Audit Chain Service

Implements tamper-evident audit logging with hash chaining.
"""

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.utils import timezone

from .models import AuditChainCheckpoint, AuditLog
from .services import SecurityService

logger = logging.getLogger(__name__)


@dataclass
class AuditRecord:
    """Individual audit record."""

    id: str
    timestamp: Any
    actor_id: str | None
    action_type: str
    target_type: str
    target_id: str
    metadata: dict
    prev_hash: str
    event_hash: str


class ImmutableAuditService:
    """
    Provides immutable audit logging with hash chain integrity.
    """

    GENESIS_HASH = "0000000000000000000000000000000000000000000000000000000000000000"

    @staticmethod
    def log_event(
        action_type: str,
        target_type: str,
        actor=None,
        chama=None,
        target_id: str = "",
        metadata: dict | None = None,
        ip_address: str = None,
    ) -> AuditLog:
        """Log an immutable audit event."""
        audit_log = SecurityService.create_audit_log(
            actor=actor,
            chama=chama,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata,
            ip_address=ip_address,
        )

        logger.info(f"Audit event logged: {action_type} on {target_type}:{target_id}")
        return audit_log

    @staticmethod
    def _get_last_record() -> AuditLog | None:
        """Get the last audit record."""
        return AuditLog.objects.order_by("-created_at").first()

    @staticmethod
    def verify_chain_integrity() -> tuple[bool, str]:
        """
        Verify audit chain integrity.
        Returns (is_valid, message).
        """
        records = list(AuditLog.objects.order_by("chain_index", "created_at")[:5000])

        if not records:
            return True, "Chain is empty"

        prev_hash = ImmutableAuditService.GENESIS_HASH

        for record in records:
            if record.chain_index < 1:
                return False, f"Invalid chain index for record {record.id}"
            if record.prev_hash != prev_hash:
                return False, f"Chain linkage mismatch at record {record.id}"

            payload = {
                "action_type": record.action_type,
                "target_type": record.target_type,
                "target_id": record.target_id,
                "actor_id": str(record.actor_id) if record.actor_id else None,
                "chama_id": str(record.chama_id) if record.chama_id else None,
                "metadata": record.metadata,
                "ip_address": record.ip_address,
                "trace_id": record.trace_id,
            }

            computed_hash = SecurityService._hash_audit_event(
                prev_hash=prev_hash,
                payload=payload,
            )

            if computed_hash != record.event_hash:
                return False, f"Chain broken at record {record.id}"

            prev_hash = computed_hash

        return True, "Chain is valid"

    @staticmethod
    def get_chain_checkpoint() -> dict:
        """Get current chain checkpoint for export."""
        last_record = ImmutableAuditService._get_last_record()

        return {
            "last_record_id": str(last_record.id) if last_record else None,
            "last_event_hash": last_record.event_hash if last_record else ImmutableAuditService.GENESIS_HASH,
            "last_chain_index": last_record.chain_index if last_record else 0,
            "record_count": AuditLog.objects.count(),
            "timestamp": timezone.now().isoformat(),
        }

    @staticmethod
    def create_daily_checkpoint() -> dict:
        """Create daily checkpoint that's signed and exported."""
        checkpoint_model = SecurityService.create_audit_checkpoint()
        checkpoint = ImmutableAuditService.get_chain_checkpoint()

        logger.info(
            f"Daily checkpoint created: {checkpoint['record_count']} records, "
            f"hash: {checkpoint['last_event_hash'][:16]}..."
        )

        return {
            **checkpoint,
            "signature": checkpoint_model.signature,
            "checkpoint_id": str(checkpoint_model.id),
        }


class FinancialAuditService:
    """
    Specialized audit service for financial events.
    """

    @staticmethod
    def log_contribution(
        member,
        chama,
        amount: str,
        actor=None,
        ip_address: str = None,
    ):
        """Log a contribution."""
        return ImmutableAuditService.log_event(
            action_type="contribution",
            target_type="wallet",
            actor=actor,
            chama=chama,
            target_id=str(member.id),
            metadata={"amount": amount, "type": "credit"},
            ip_address=ip_address,
        )

    @staticmethod
    def log_loan_disbursement(
        loan,
        amount: str,
        actor=None,
        ip_address: str = None,
    ):
        """Log loan disbursement."""
        return ImmutableAuditService.log_event(
            action_type="loan_disbursement",
            target_type="loan",
            actor=actor,
            chama=loan.member.chama,
            target_id=str(loan.id),
            metadata={"amount": amount, "member_id": str(loan.member.user_id)},
            ip_address=ip_address,
        )

    @staticmethod
    def log_loan_repayment(
        loan,
        amount: str,
        actor=None,
        ip_address: str = None,
    ):
        """Log loan repayment."""
        return ImmutableAuditService.log_event(
            action_type="loan_repayment",
            target_type="loan",
            actor=actor,
            chama=loan.member.chama,
            target_id=str(loan.id),
            metadata={"amount": amount},
            ip_address=ip_address,
        )

    @staticmethod
    def log_withdrawal(
        member,
        chama,
        amount: str,
        actor=None,
        ip_address: str = None,
    ):
        """Log a withdrawal."""
        return ImmutableAuditService.log_event(
            action_type="withdrawal",
            target_type="wallet",
            actor=actor,
            chama=chama,
            target_id=str(member.id),
            metadata={"amount": amount, "type": "debit"},
            ip_address=ip_address,
        )

    @staticmethod
    def log_role_change(
        member,
        old_role: str,
        new_role: str,
        actor=None,
        ip_address: str = None,
    ):
        """Log role change."""
        return ImmutableAuditService.log_event(
            action_type="role_change",
            target_type="membership",
            actor=actor,
            chama=member.chama,
            target_id=str(member.id),
            metadata={"old_role": old_role, "new_role": new_role},
            ip_address=ip_address,
        )

    @staticmethod
    def log_approval(
        resource_type: str,
        resource_id: str,
        approval_stage: str,
        actor=None,
        chama=None,
        ip_address: str = None,
    ):
        """Log approval action."""
        return ImmutableAuditService.log_event(
            action_type="approval",
            target_type=resource_type,
            actor=actor,
            chama=chama,
            target_id=resource_id,
            metadata={"stage": approval_stage},
            ip_address=ip_address,
        )


__all__ = [
    "ImmutableAuditService",
    "FinancialAuditService",
    "AuditRecord",
]
