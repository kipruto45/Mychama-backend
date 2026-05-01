from __future__ import annotations

from core.audit import create_audit_log

from apps.accounts.models import KYCEvent, MemberKYC


def log_kyc_event(
    *,
    kyc_record: MemberKYC,
    event_type: str,
    code: str,
    message: str,
    actor=None,
    metadata: dict | None = None,
) -> KYCEvent:
    payload = metadata or {}
    event = KYCEvent.objects.create(
        kyc_record=kyc_record,
        user=kyc_record.user,
        actor=actor,
        event_type=event_type,
        code=code,
        message=message,
        metadata=payload,
    )
    create_audit_log(
        actor=actor or kyc_record.user,
        action=f"kyc_{event_type}",
        entity_type="MemberKYC",
        entity_id=kyc_record.id,
        chama_id=kyc_record.chama_id,
        metadata={"code": code, "message": message, **payload},
    )
    return event
