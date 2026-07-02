import logging
from typing import Any

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.agent.state import AgentState
from app.mcp.server import MCPServer
from app.workflow import ExecutorNode, FormatterNode, LLMPlannerNode


class AgentGraph:
    """Execution framework for the agent.

    The graph is deliberately thin: the planner (the agent) decides every step,
    the executor runs the tool it chose, and control returns to the planner
    until it decides to finish, ask for clarification, or fails to reason. The
    framework routes; it never decides.
    """

    def __init__(self, session: Session) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self._available_tools: list[dict[str, Any]] = []
        self._graph = self._build(session)

    def _build(self, session: Session) -> StateGraph:
        mcp = MCPServer(session)
        self._available_tools = mcp.registry.list_tools()

        planner = LLMPlannerNode()
        executor = ExecutorNode(mcp)
        formatter = FormatterNode()

        workflow = StateGraph(AgentState)
        workflow.add_node("planner", planner.run)
        workflow.add_node("executor", executor.run)
        workflow.add_node("formatter", formatter.run)

        workflow.set_entry_point("planner")
        workflow.add_conditional_edges(
            "planner",
            self._route_from_planner,
            {"executor": "executor", "formatter": "formatter"},
        )
        workflow.add_edge("executor", "planner")
        workflow.add_edge("formatter", END)

        return workflow.compile()

    def _initial_state(self, query: str) -> AgentState:
        return {
            "user_query": query,
            "available_tools": list(self._available_tools),
            "reasoning_trace": [],
            "steps": [],
            "next_action": None,
            "iteration_count": 0,
            "confidence_score": 0.0,
            "final_response": None,
            "error": None,
            "execution_events": [],
            "execution_logs": [],
            "execution_timeline": [],
            "executed_tools": [],
            "execution_plan": [],
        }

    def invoke(self, query: str) -> dict[str, Any]:
        self.logger.info("Agent graph invoked", extra={"query": query})
        result = self._graph.invoke(self._initial_state(query))
        self.logger.info("Agent graph completed")
        return result

    def stream(self, query: str):
        self.logger.info("Agent graph streaming", extra={"query": query})
        state: dict[str, Any] = dict(self._initial_state(query))
        for chunk in self._graph.stream(state):
            for node_name, update in chunk.items():
                state = {**state, **update}
                yield {
                    "node": node_name,
                    "execution_events": state.get("execution_events", []),
                    "next_action": state.get("next_action"),
                    "final_response": state.get("final_response"),
                    "error": state.get("error"),
                    "state": state,
                }

    @staticmethod
    def _route_from_planner(state: AgentState) -> str:
        next_action = state.get("next_action") or {}
        if next_action.get("action") == "call_tool":
            return "executor"
        return "formatter"
