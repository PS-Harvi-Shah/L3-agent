"""Persistence of agent execution traces.

Every agent run is written to ``executions/<execution_id>.json`` exactly as
it was returned to the caller, so the full reasoning trace, tool calls, and
events can be audited and replayed later.
"""

import json
import logging
from pathlib import Path
from typing import Any


logger = logging.getLogger("agent.audit")

_EXECUTIONS_DIR = Path(__file__).resolve().parent.parent.parent / "executions"


def persist_execution(result: dict[str, Any]) -> Path | None:
    execution_id = result.get("execution_id", "unknown")
    try:
        _EXECUTIONS_DIR.mkdir(exist_ok=True)
        filepath = _EXECUTIONS_DIR / f"{execution_id}.json"
        filepath.write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )
        logger.info(
            "Execution trace persisted",
            extra={"execution_id": execution_id, "file": str(filepath)},
        )
        return filepath
    except OSError:
        logger.exception(
            "Failed to persist execution trace", extra={"execution_id": execution_id}
        )
        return None


def load_execution(execution_id: str) -> dict[str, Any] | None:
    filepath = _EXECUTIONS_DIR / f"{execution_id}.json"
    if not filepath.exists():
        return None
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to load execution trace", extra={"execution_id": execution_id})
        return None


def list_executions(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    if not _EXECUTIONS_DIR.exists():
        return []
    files = sorted(
        _EXECUTIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    summaries: list[dict[str, Any]] = []
    for filepath in files[offset : offset + limit]:
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        summaries.append(
            {
                "execution_id": data.get("execution_id", filepath.stem),
                "query": data.get("query", ""),
                "status": data.get("status", "unknown"),
                "duration_ms": data.get("duration_ms", 0.0),
                "steps": len(data.get("reasoning_trace", [])),
                "tool_calls": len(data.get("tool_calls", [])),
                "counts": data.get("counts", {}),
                "error": data.get("error"),
            }
        )
    return summaries
