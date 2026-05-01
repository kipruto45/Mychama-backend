from core.models import ActivityLog, AuditLog
from core.request_context import get_correlation_id


def create_audit_log(
    *,
    action: str,
    entity_type: str,
    actor=None,
    chama_id=None,
    entity_id=None,
    metadata: dict | None = None,
    trace_id: str | None = None,
):
    normalized_trace = (trace_id or get_correlation_id() or "").strip()

    return AuditLog.objects.create(
        actor=actor,
        chama_id=chama_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata or {},
        trace_id=normalized_trace,
    )


def create_activity_log(
    *,
    action: str,
    entity_type: str,
    actor=None,
    chama_id=None,
    entity_id=None,
    metadata: dict | None = None,
    trace_id: str | None = None,
):
    normalized_trace = (trace_id or get_correlation_id() or "").strip()

    return ActivityLog.objects.create(
        actor=actor,
        chama_id=chama_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata or {},
        trace_id=normalized_trace,
    )
