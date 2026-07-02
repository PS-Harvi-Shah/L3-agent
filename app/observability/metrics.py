import logging
from typing import Any

from app.observability.audit import AuditStore


class MetricsCollector:
    def __init__(self, audit_store: AuditStore | None = None) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self._audit = audit_store or AuditStore()

    def get_aggregated_metrics(self) -> dict[str, Any]:
        executions = self._audit.list_executions(limit=1000)

        if not executions:
            return self._empty_metrics()

        total_count = len(executions)
        success_count = sum(1 for e in executions if e.get("success", False))
        failure_count = total_count - success_count

        total_times = [e.get("execution_duration_ms", 0) for e in executions]
        total_tools = [e.get("total_tools_executed", 0) for e in executions]
        confidences = [e.get("confidence_score", 0.0) for e in executions]

        tool_latencies: dict[str, list[float]] = {}
        db_query_counts: list[int] = []

        for execution in executions:
            eid = execution.get("execution_id", "")
            trace = self._audit.load(eid)
            if not trace:
                continue

            for tool_name, latency in trace.get("tool_latencies", {}).items():
                tool_latencies.setdefault(tool_name, []).append(latency)

            db_queries = trace.get("total_db_queries", 0)
            if db_queries > 0:
                db_query_counts.append(db_queries)

        avg_tool_latencies = {
            tool: round(sum(lats) / len(lats), 2)
            for tool, lats in tool_latencies.items()
        }

        return {
            "total_executions": total_count,
            "success_count": success_count,
            "failure_count": failure_count,
            "success_rate": round(success_count / max(total_count, 1), 4),
            "failure_rate": round(failure_count / max(total_count, 1), 4),
            "avg_execution_time_ms": round(sum(total_times) / max(len(total_times), 1), 2),
            "min_execution_time_ms": round(min(total_times), 2) if total_times else 0,
            "max_execution_time_ms": round(max(total_times), 2) if total_times else 0,
            "avg_tools_executed": round(sum(total_tools) / max(len(total_tools), 1), 2),
            "min_tools_executed": min(total_tools) if total_tools else 0,
            "max_tools_executed": max(total_tools) if total_tools else 0,
            "avg_confidence": round(sum(confidences) / max(len(confidences), 1), 4),
            "avg_tool_latencies_ms": avg_tool_latencies,
            "total_tool_types": len(avg_tool_latencies),
            "avg_db_queries_per_execution": (
                round(sum(db_query_counts) / max(len(db_query_counts), 1), 2)
                if db_query_counts
                else 0
            ),
        }

    def _empty_metrics(self) -> dict[str, Any]:
        return {
            "total_executions": 0,
            "success_count": 0,
            "failure_count": 0,
            "success_rate": 0.0,
            "failure_rate": 0.0,
            "avg_execution_time_ms": 0.0,
            "min_execution_time_ms": 0.0,
            "max_execution_time_ms": 0.0,
            "avg_tools_executed": 0.0,
            "min_tools_executed": 0,
            "max_tools_executed": 0,
            "avg_confidence": 0.0,
            "avg_tool_latencies_ms": {},
            "total_tool_types": 0,
            "avg_db_queries_per_execution": 0.0,
        }
