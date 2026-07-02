import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class WorkflowStepTrace:
    node_name: str
    tool_invoked: str | None = None
    input_summary: str | None = None
    output_summary: str | None = None
    execution_time_ms: float = 0.0
    status: str = "unknown"
    timestamp: str = ""


@dataclass
class ExecutionTrace:
    execution_id: str
    user_query: str
    timestamp: str
    entity_type: str | None = None
    identifier_type: str | None = None
    identifier_value: str | None = None
    execution_plan: list[str] = field(default_factory=list)
    executed_tools: list[str] = field(default_factory=list)
    tool_execution_order: list[str] = field(default_factory=list)
    execution_duration_ms: float = 0.0
    retrieved_entities: dict[str, Any] = field(default_factory=dict)
    missing_entities: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    confidence_score: float = 0.0
    final_response: dict[str, Any] | None = None
    error: str | None = None
    workflow_steps: list[dict[str, Any]] = field(default_factory=list)
    tool_latencies: dict[str, float] = field(default_factory=dict)
    planner_latency_ms: float = 0.0
    llm_latency_ms: float = 0.0
    total_tools_executed: int = 0
    total_db_queries: int = 0
    success: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionTrace":
        return cls(**data)


class TraceManager:
    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)

    def create_trace(
        self,
        query: str,
        start_time: float | None = None,
    ) -> ExecutionTrace:
        execution_id = uuid.uuid4().hex[:16]
        now = datetime.now(timezone.utc).isoformat()
        return ExecutionTrace(
            execution_id=execution_id,
            user_query=query,
            timestamp=now,
        )

    def finalize_trace(
        self,
        trace: ExecutionTrace,
        result: dict[str, Any],
        start_time: float,
        end_time: float,
    ) -> ExecutionTrace:
        trace.execution_duration_ms = round((end_time - start_time) * 1000, 2)
        trace.entity_type = result.get("entity_type")
        trace.identifier_type = result.get("identifier_type")
        trace.identifier_value = result.get("identifier_value")
        trace.execution_plan = result.get("execution_plan", [])
        trace.executed_tools = result.get("executed_tools", [])
        trace.tool_execution_order = list(result.get("executed_tools", []))
        trace.retrieved_entities = result.get("retrieved_entities", {})
        trace.missing_entities = result.get("missing_entities", [])
        trace.missing_information = result.get("missing_information", [])
        trace.confidence_score = result.get("confidence_score", 0.0)
        trace.total_tools_executed = len(result.get("executed_tools", []))

        final = result.get("final_response")
        if final:
            trace.final_response = final
            trace.error = final.get("error")

        if not trace.error and result.get("error"):
            trace.error = result.get("error")

        trace.success = trace.error is None

        timeline = result.get("execution_timeline", [])
        for entry in timeline:
            tool_name = entry.get("tool", "")
            latency = entry.get("execution_time_ms", 0)
            if tool_name:
                trace.tool_latencies[tool_name] = round(latency, 2)

        logs = result.get("execution_logs", [])
        if not logs:
            for event in result.get("execution_events", []):
                logs.append(
                    {
                        "node": event.get("node", ""),
                        "status": event.get("phase", ""),
                        "detail": event.get("message", ""),
                    }
                )
        for log_entry in logs:
            step: dict[str, Any] = {
                "node_name": log_entry.get("node", ""),
                "tool_invoked": log_entry.get("tool") or log_entry.get("detail", ""),
                "input_summary": log_entry.get("entity_type", ""),
                "output_summary": log_entry.get("status", ""),
                "execution_time_ms": log_entry.get("execution_time_ms", 0),
                "status": log_entry.get("status", "unknown"),
            }
            trace.workflow_steps.append(step)

        planner_logs = [l for l in logs if l.get("node") in ("llm_planner", "planner")]
        if planner_logs:
            trace.planner_latency_ms = round(
                sum(l.get("execution_time_ms", 0) for l in planner_logs), 2
            )

        return trace

    def add_workflow_step(
        self,
        trace: ExecutionTrace,
        step: WorkflowStepTrace,
    ) -> None:
        trace.workflow_steps.append(asdict(step))

    def compute_db_query_count(self, trace: ExecutionTrace) -> int:
        count = 0
        for step in trace.workflow_steps:
            if step.get("node_name") in ("executor", "entity_resolver"):
                count += 1
        trace.total_db_queries = count
        return count
