import logging
from typing import Any

from app.agent.events import merge_events, publish_event
from app.agent.state import AgentState


class FormatterNode:
    """Renders the agent's terminal decision into the final response. It draws
    the status directly from the agent's own decision and simply consolidates
    the records the tools returned — it never re-judges completeness."""

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)

    def run(self, state: AgentState) -> dict:
        decision = state.get("next_action") or {}
        action = decision.get("action")
        steps = state.get("steps", [])

        products, suppliers = self._consolidate(steps)
        status = self._status(action, decision, products, suppliers)

        payload: dict[str, Any] = {
            "query": state.get("user_query", ""),
            "status": status,
            "answer": decision.get("answer"),
            "clarification": decision.get("clarification"),
            "assessment": decision.get("assessment"),
            "confidence_score": round(state.get("confidence_score", 0.0), 2),
            "consolidated_data": {"products": products, "suppliers": suppliers},
            "counts": {"products": len(products), "suppliers": len(suppliers)},
            "reasoning_trace": state.get("reasoning_trace", []),
            "tool_calls": self._tool_calls(steps),
            "executed_tools": state.get("executed_tools", []),
            "execution_plan": state.get("execution_plan", []),
            "execution_timeline": state.get("execution_timeline", []),
            "error": decision.get("error") or state.get("error"),
        }

        event = publish_event(
            "formatter",
            status,
            self._summary_message(status, products, suppliers, decision),
            data={"status": status, "confidence_score": payload["confidence_score"]},
        )
        all_events = merge_events(state, [event])
        payload["execution_events"] = all_events

        return {
            "final_response": payload,
            "execution_events": all_events,
            "execution_logs": [{"node": "formatter", "status": status}],
        }

    @staticmethod
    def _status(
        action: str | None,
        decision: dict[str, Any],
        products: list[dict[str, Any]],
        suppliers: list[dict[str, Any]],
    ) -> str:
        if action == "error" or decision.get("error"):
            return "error"
        if action == "clarify":
            return "needs_clarification"
        if decision.get("terminal") == "max_iterations":
            return "incomplete"
        if products or suppliers:
            return "complete"
        return "no_results"

    def _consolidate(
        self, steps: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        products: list[dict[str, Any]] = []
        suppliers: list[dict[str, Any]] = []
        for step in steps:
            data = step.get("data")
            if not isinstance(data, dict):
                continue
            self._merge(products, data.get("products"))
            self._merge(suppliers, data.get("suppliers"))
        return products, suppliers

    @staticmethod
    def _merge(target: list[dict[str, Any]], records: Any) -> None:
        if not isinstance(records, list):
            return
        seen = {r.get("id") for r in target}
        for record in records:
            if isinstance(record, dict) and record.get("id") not in seen:
                target.append(record)
                seen.add(record.get("id"))

    @staticmethod
    def _tool_calls(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        calls = []
        for step in steps:
            data = step.get("data")
            record_count = 0
            if isinstance(data, dict):
                record_count = len(data.get("products", []) or []) + len(data.get("suppliers", []) or [])
            calls.append(
                {
                    "step": step.get("step"),
                    "tool": step.get("tool"),
                    "tool_input": step.get("tool_input"),
                    "success": step.get("success"),
                    "error": step.get("error"),
                    "record_count": record_count,
                    "execution_time_ms": step.get("execution_time_ms"),
                }
            )
        return calls

    @staticmethod
    def _summary_message(
        status: str,
        products: list[dict[str, Any]],
        suppliers: list[dict[str, Any]],
        decision: dict[str, Any],
    ) -> str:
        if status == "error":
            return decision.get("error") or "Agent could not complete the request"
        if status == "needs_clarification":
            return decision.get("clarification") or "Agent requires clarification"
        if status == "no_results":
            return "Agent finished but found no matching records"
        return f"Consolidated {len(products)} product(s) and {len(suppliers)} supplier(s)"
