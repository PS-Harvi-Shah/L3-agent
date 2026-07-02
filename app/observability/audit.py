import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.observability.trace import ExecutionTrace


_EXECUTIONS_DIR: str | None = None


def _get_executions_dir() -> str:
    global _EXECUTIONS_DIR
    if _EXECUTIONS_DIR is not None:
        return _EXECUTIONS_DIR
    settings = get_settings()
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _EXECUTIONS_DIR = os.path.join(base_dir, "executions")
    os.makedirs(_EXECUTIONS_DIR, exist_ok=True)
    return _EXECUTIONS_DIR


class AuditStore:
    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self._store_dir = _get_executions_dir()

    def persist(self, trace: ExecutionTrace) -> str:
        filepath = os.path.join(self._store_dir, f"{trace.execution_id}.json")
        payload = trace.to_dict()
        payload["_stored_at"] = datetime.now(timezone.utc).isoformat()
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, default=str)
            self.logger.info(
                "Execution trace persisted",
                extra={"execution_id": trace.execution_id, "file": filepath},
            )
            return filepath
        except OSError as exc:
            self.logger.error(
                "Failed to persist execution trace",
                extra={"execution_id": trace.execution_id, "error": str(exc)},
            )
            raise

    def load(self, execution_id: str) -> dict[str, Any] | None:
        filepath = os.path.join(self._store_dir, f"{execution_id}.json")
        if not os.path.exists(filepath):
            return None
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.error(
                "Failed to load execution trace",
                extra={"execution_id": execution_id, "error": str(exc)},
            )
            return None

    def list_executions(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        try:
            files = sorted(
                [f for f in os.listdir(self._store_dir) if f.endswith(".json")],
                reverse=True,
            )
        except OSError:
            return []

        selected = files[offset : offset + limit]
        result: list[dict[str, Any]] = []
        for filename in selected:
            filepath = os.path.join(self._store_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                result.append({
                    "execution_id": data.get("execution_id", filename.replace(".json", "")),
                    "user_query": data.get("user_query", ""),
                    "timestamp": data.get("timestamp", ""),
                    "execution_duration_ms": data.get("execution_duration_ms", 0),
                    "confidence_score": data.get("confidence_score", 0.0),
                    "success": data.get("success", True),
                    "total_tools_executed": data.get("total_tools_executed", 0),
                    "error": data.get("error"),
                })
            except (OSError, json.JSONDecodeError):
                continue

        return result

    def delete(self, execution_id: str) -> bool:
        filepath = os.path.join(self._store_dir, f"{execution_id}.json")
        if not os.path.exists(filepath):
            return False
        try:
            os.remove(filepath)
            return True
        except OSError:
            return False
