import logging
from datetime import datetime, timezone
from typing import Any

from app.observability.audit import AuditStore
from app.observability.trace import ExecutionTrace


class EvaluationReport:
    def __init__(
        self,
        execution_id: str,
        completeness: float,
        correctness: float,
        tool_selection_quality: float,
        overall_confidence: float,
        missing_count: int,
        total_expected: int,
        details: dict[str, Any],
    ) -> None:
        self.execution_id = execution_id
        self.completeness = completeness
        self.correctness = correctness
        self.tool_selection_quality = tool_selection_quality
        self.overall_confidence = overall_confidence
        self.missing_count = missing_count
        self.total_expected = total_expected
        self.details = details
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        overall = round(
            (
                self.completeness
                + self.correctness
                + self.tool_selection_quality
                + self.overall_confidence
            )
            / 4.0,
            4,
        )
        return {
            "execution_id": self.execution_id,
            "timestamp": self.timestamp,
            "overall_score": overall,
            "completeness": round(self.completeness, 4),
            "correctness": round(self.correctness, 4),
            "tool_selection_quality": round(self.tool_selection_quality, 4),
            "overall_confidence": round(self.overall_confidence, 4),
            "missing_count": self.missing_count,
            "total_expected": self.total_expected,
            "details": self.details,
        }


class EvaluationEngine:
    def __init__(self, audit_store: AuditStore | None = None) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self._audit = audit_store or AuditStore()

    def evaluate(self, execution_id: str) -> EvaluationReport | None:
        raw = self._audit.load(execution_id)
        if raw is None:
            return None

        try:
            trace = ExecutionTrace.from_dict(raw)
        except (TypeError, KeyError) as exc:
            self.logger.error(
                "Failed to parse execution trace",
                extra={"execution_id": execution_id, "error": str(exc)},
            )
            return None

        return self._evaluate_trace(trace, raw)

    def _evaluate_trace(
        self, trace: ExecutionTrace, raw: dict[str, Any]
    ) -> EvaluationReport:
        completeness = self._score_completeness(trace)
        correctness = self._score_correctness(trace)
        tool_quality = self._score_tool_selection(trace)
        overall_conf = self._score_confidence(trace)

        missing_count = len(trace.missing_entities) + len(trace.missing_information)
        total_expected = max(len(trace.execution_plan), 1)

        details = {
            "plan_tools": trace.execution_plan,
            "executed_tools": trace.executed_tools,
            "entities_found": list(trace.retrieved_entities.keys()),
            "missing_entities": trace.missing_entities,
            "missing_info": trace.missing_information,
            "errors": trace.error,
            "workflow_steps": len(trace.workflow_steps),
            "latencies": trace.tool_latencies,
        }

        return EvaluationReport(
            execution_id=trace.execution_id,
            completeness=completeness,
            correctness=correctness,
            tool_selection_quality=tool_quality,
            overall_confidence=overall_conf,
            missing_count=missing_count,
            total_expected=total_expected,
            details=details,
        )

    def _score_completeness(self, trace: ExecutionTrace) -> float:
        if not trace.execution_plan:
            return 1.0
        executed = set(trace.executed_tools)
        planned = set(trace.execution_plan)
        if not planned:
            return 1.0
        return len(executed & planned) / len(planned)

    def _score_correctness(self, trace: ExecutionTrace) -> float:
        if trace.error:
            return 0.0
        if trace.final_response:
            error_field = trace.final_response.get("error")
            if error_field:
                return 0.0
        if trace.total_tools_executed == 0:
            return 0.5
        successful = sum(
            1 for t in trace.executed_tools
            if trace.tool_latencies.get(t, 0) > 0
        )
        return successful / max(trace.total_tools_executed, 1)

    def _score_tool_selection(self, trace: ExecutionTrace) -> float:
        if not trace.execution_plan:
            return 0.5
        if not trace.executed_tools:
            return 0.0
        relevant = sum(
            1 for t in trace.executed_tools
            if t in trace.execution_plan
        )
        if not relevant:
            return 0.0
        precision = relevant / len(trace.executed_tools)
        recall = relevant / len(trace.execution_plan)
        if precision + recall == 0:
            return 0.0
        f1 = 2 * (precision * recall) / (precision + recall)
        return f1

    def _score_confidence(self, trace: ExecutionTrace) -> float:
        return trace.confidence_score
