from typing import Any, TypedDict


class AgentState(TypedDict):
    """State shared across the agent loop.

    The agent (planner) is the only decision-maker. The framework carries its
    reasoning, the observations produced by tool execution, and the records
    needed to render a transparent trace — but it never decides anything itself.
    """

    user_query: str
    available_tools: list[dict[str, Any]]

    # Agent reasoning + tool observations (the ReAct trace)
    reasoning_trace: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    next_action: dict[str, Any] | None
    iteration_count: int
    confidence_score: float

    # Output
    final_response: dict[str, Any] | None
    error: str | None

    # Execution records surfaced to the UI / observability (not decisions)
    execution_events: list[dict[str, Any]]
    execution_logs: list[dict[str, Any]]
    execution_timeline: list[dict[str, Any]]
    executed_tools: list[str]
    execution_plan: list[str]
