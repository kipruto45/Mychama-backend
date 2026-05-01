from contextvars import ContextVar

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value or "")


def get_correlation_id() -> str:
    return _correlation_id.get("")


def clear_correlation_id() -> None:
    _correlation_id.set("")
