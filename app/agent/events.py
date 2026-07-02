from datetime import UTC, datetime
from typing import Any


def publish_event(
    node: str,
    phase: str,
    message: str,
    *,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "node": node,
        "phase": phase,
        "message": message,
        "data": data or {},
    }


def merge_events(state: dict[str, Any], new_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing = list(state.get("execution_events", []))
    existing.extend(new_events)
    return existing
