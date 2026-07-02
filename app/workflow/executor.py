import logging
import time
from typing import Any

from app.agent.events import merge_events, publish_event
from app.agent.state import AgentState
from app.mcp.server import MCPServer


class ExecutorNode:
    """Runs the tool the agent chose and records the observation. It makes no
    decisions — it only executes and reports back what happened."""

    def __init__(self, mcp: MCPServer) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self._mcp = mcp

    def run(self, state: AgentState) -> dict:
        next_action = state.get("next_action") or {}
        tool_name = next_action.get("tool")
        tool_input = next_action.get("tool_input") or {}

        step_number = len(state.get("steps", [])) + 1
        start_event = publish_event(
            "executor",
            "started",
            f"Executing {tool_name} with {tool_input}",
            data={"tool": tool_name, "tool_input": tool_input},
        )

        self.logger.info("Executing tool", extra={"tool": tool_name, "tool_input": tool_input})
        start = time.perf_counter()
        try:
            result = self._mcp.execute_tool(tool_name, tool_input)
            success = bool(result.get("success"))
            data = result.get("data")
            error = result.get("error")
        except Exception as exc:
            self.logger.exception("Tool execution failed", extra={"tool": tool_name})
            success = False
            data = None
            error = str(exc)
        elapsed = round((time.perf_counter() - start) * 1000, 2)

        observation = {
            "step": step_number,
            "tool": tool_name,
            "tool_input": tool_input,
            "success": success,
            "data": data if success else None,
            "error": error,
            "execution_time_ms": elapsed,
        }

        steps = list(state.get("steps", []))
        steps.append(observation)

        executed_tools = list(state.get("executed_tools", []))
        if tool_name:
            executed_tools.append(tool_name)

        execution_timeline = list(state.get("execution_timeline", []))
        execution_timeline.append(
            {
                "step": step_number,
                "tool": tool_name,
                "status": "success" if success else "error",
                "execution_time_ms": elapsed,
                "confidence": next_action.get("confidence", 0.0),
            }
        )

        record_count = self._record_count(data) if success else 0
        complete_event = publish_event(
            "executor",
            "completed" if success else "error",
            (
                f"{tool_name} returned {record_count} record(s) in {elapsed}ms"
                if success
                else f"{tool_name} failed: {error}"
            ),
            data={
                "tool": tool_name,
                "success": success,
                "record_count": record_count,
                "execution_time_ms": elapsed,
                "error": error,
            },
        )

        execution_logs = list(state.get("execution_logs", []))
        execution_logs.append(
            {
                "node": "executor",
                "status": "success" if success else "error",
                "tool": tool_name,
                "execution_time_ms": elapsed,
            }
        )

        return {
            "steps": steps,
            "executed_tools": executed_tools,
            "execution_timeline": execution_timeline,
            "execution_logs": execution_logs,
            "execution_events": merge_events(state, [start_event, complete_event]),
        }

    @staticmethod
    def _record_count(data: Any) -> int:
        if isinstance(data, dict):
            return len(data.get("products", []) or []) + len(data.get("suppliers", []) or [])
        return 0
