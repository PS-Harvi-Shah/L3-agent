import json
import logging
from typing import Any

from app.agent.events import merge_events, publish_event
from app.agent.state import AgentState
from app.llm import get_llm_provider


# Safety guardrail only — it bounds the loop, it does not make decisions.
MAX_ITERATIONS = 8

_SYSTEM_PROMPT = """You are the reasoning core of a Master Data Discovery Agent. You are the \
ONLY decision-maker: you interpret the user's identifier, choose which tool to run and with \
what arguments, judge whether the consolidated data satisfies the goal, and decide when to \
stop. Nothing else in the system reasons on your behalf.

## Goal
Consolidate product master data from the enterprise databases for the user's request. The user \
may supply any identifier (product id, product name, part number, supplier id, supplier name).

## User request
{user_query}

## Tools you can call
{tools}

## What has happened so far (observations)
{history}

Iteration {iteration} of {max_iterations}.

## How to decide
1. Infer from the raw request which kind of identifier the user gave. Do NOT assume — reason \
about the value's shape and any wording. If it is genuinely ambiguous (e.g. a bare number that \
could be a product id or a supplier id), pick the most likely interpretation and try it; if it \
returns nothing, try the alternative on the next turn.
2. Choose the tool whose description best fits the goal, and build its arguments strictly from \
its parameter schema.
3. After each observation, judge completeness YOURSELF from the records returned. Decide whether \
more data is needed or the goal is satisfied.
4. Stop with action "finish" when the retrieved data answers the request. Use "clarify" only \
when you cannot proceed at all (no viable identifier interpretation). Never invent tools or \
identifier types outside the schemas above.

## Respond with STRICT JSON only (no prose, no markdown), matching:
{{
  "thought": "your reasoning about the current state",
  "assessment": "what has been gathered and what, if anything, is still missing",
  "action": "call_tool" | "finish" | "clarify",
  "tool": "<tool name, or null>",
  "tool_input": {{ ...arguments matching the tool's schema... }} or null,
  "confidence": 0.0-1.0,
  "answer": "<concise natural-language answer when finishing, else null>",
  "clarification": "<question to the user when clarifying, else null>"
}}"""

_ACTION_ALIASES = {
    "call_tool": "call_tool",
    "invoke_tool": "call_tool",
    "tool": "call_tool",
    "finish": "finish",
    "complete": "finish",
    "done": "finish",
    "answer": "finish",
    "clarify": "clarify",
    "clarification": "clarify",
    "ask": "clarify",
}


class LLMPlannerNode:
    """The agent. Turns state into the next decision using the LLM, or fails
    gracefully when it cannot reason confidently. No heuristic fallbacks."""

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self._llm = get_llm_provider()

    def run(self, state: AgentState) -> dict:
        available_tools = state.get("available_tools", [])
        iteration = state.get("iteration_count", 0) + 1

        registry_event = publish_event(
            "tool_registry",
            "catalog",
            f"Registry exposes {len(available_tools)} tool(s) to the agent",
            data={"tools": [t.get("name") for t in available_tools]},
        )

        if iteration > MAX_ITERATIONS:
            decision = self._stop(
                "finish",
                thought="Iteration budget exhausted before the goal was confidently satisfied.",
                assessment="Returning the best data gathered so far.",
                answer="Reached the maximum number of reasoning steps.",
                confidence=state.get("confidence_score", 0.0),
                terminal="max_iterations",
            )
            return self._emit(state, decision, iteration, [registry_event])

        if not self._llm.is_available:
            decision = self._stop(
                "error",
                thought="The reasoning model is unavailable, so no confident decision can be made.",
                assessment="Cannot plan without the agent's reasoning model.",
                error="Reasoning model unavailable — the agent cannot decide the next action.",
            )
            return self._emit(state, decision, iteration, [registry_event])

        try:
            raw = self._reason(state, available_tools, iteration)
        except Exception as exc:  # LLM/transport/parse failure
            self.logger.warning("Agent reasoning failed", extra={"error": str(exc)})
            decision = self._stop(
                "error",
                thought="The agent could not produce a valid decision.",
                assessment="Reasoning step failed.",
                error=f"Agent reasoning failed: {exc}",
            )
            return self._emit(state, decision, iteration, [registry_event])

        decision = self._normalize(raw)
        if decision is None:
            decision = self._stop(
                "error",
                thought="The agent returned an unusable decision.",
                assessment="Could not interpret the agent's output.",
                error="Agent produced an invalid decision and cannot continue.",
            )
        return self._emit(state, decision, iteration, [registry_event])

    def _reason(
        self, state: AgentState, available_tools: list[dict[str, Any]], iteration: int
    ) -> dict[str, Any]:
        prompt = _SYSTEM_PROMPT.format(
            user_query=state.get("user_query", ""),
            tools=json.dumps(available_tools, indent=2, default=str),
            history=self._render_history(state.get("steps", [])),
            iteration=iteration,
            max_iterations=MAX_ITERATIONS,
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": state.get("user_query", "")},
        ]

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                return self._llm.chat_structured(messages, temperature=0.1)
            except Exception as exc:
                last_error = exc
                messages.append(
                    {"role": "user", "content": "Your previous reply was not valid JSON. Reply with STRICT JSON only."}
                )
        raise last_error  # type: ignore[misc]

    @staticmethod
    def _render_history(steps: list[dict[str, Any]]) -> str:
        if not steps:
            return "No tools have been executed yet."
        lines = []
        for step in steps:
            lines.append(
                json.dumps(
                    {
                        "step": step.get("step"),
                        "tool": step.get("tool"),
                        "tool_input": step.get("tool_input"),
                        "success": step.get("success"),
                        "error": step.get("error"),
                        "data": step.get("data"),
                    },
                    default=str,
                )
            )
        return "\n".join(lines)

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None

        action = _ACTION_ALIASES.get(str(raw.get("action", "")).strip().lower())
        if action is None:
            return None

        thought = str(raw.get("thought") or raw.get("reasoning") or "")
        assessment = str(raw.get("assessment") or "")
        confidence = self._as_float(raw.get("confidence"))

        if action == "call_tool":
            tool = raw.get("tool")
            if not isinstance(tool, str) or not tool.strip():
                return None
            tool_input = raw.get("tool_input")
            if not isinstance(tool_input, dict):
                tool_input = {}
            return {
                "action": "call_tool",
                "tool": tool.strip(),
                "tool_input": tool_input,
                "thought": thought,
                "assessment": assessment,
                "confidence": confidence,
                "answer": None,
                "clarification": None,
                "error": None,
            }

        if action == "clarify":
            return self._stop(
                "clarify",
                thought=thought,
                assessment=assessment,
                clarification=str(raw.get("clarification") or raw.get("answer") or "Please provide a more specific identifier."),
                confidence=confidence,
            )

        return self._stop(
            "finish",
            thought=thought,
            assessment=assessment,
            answer=str(raw.get("answer") or ""),
            confidence=confidence,
        )

    @staticmethod
    def _stop(
        action: str,
        *,
        thought: str,
        assessment: str,
        answer: str | None = None,
        clarification: str | None = None,
        error: str | None = None,
        confidence: float = 0.0,
        terminal: str | None = None,
    ) -> dict[str, Any]:
        return {
            "action": action,
            "tool": None,
            "tool_input": None,
            "thought": thought,
            "assessment": assessment,
            "confidence": confidence,
            "answer": answer,
            "clarification": clarification,
            "error": error,
            "terminal": terminal,
        }

    def _emit(
        self,
        state: AgentState,
        decision: dict[str, Any],
        iteration: int,
        extra_events: list[dict[str, Any]],
    ) -> dict:
        reasoning_trace = list(state.get("reasoning_trace", []))
        reasoning_trace.append(
            {
                "step": iteration,
                "thought": decision.get("thought"),
                "assessment": decision.get("assessment"),
                "action": decision.get("action"),
                "tool": decision.get("tool"),
                "tool_input": decision.get("tool_input"),
                "confidence": decision.get("confidence"),
            }
        )

        execution_plan = list(state.get("execution_plan", []))
        if decision["action"] == "call_tool" and decision.get("tool"):
            execution_plan.append(decision["tool"])

        message = decision.get("thought") or "Agent produced a decision"
        planner_event = publish_event(
            "planner",
            decision["action"],
            message,
            data={
                "action": decision["action"],
                "tool": decision.get("tool"),
                "tool_input": decision.get("tool_input"),
                "assessment": decision.get("assessment"),
                "confidence": decision.get("confidence"),
                "answer": decision.get("answer"),
                "clarification": decision.get("clarification"),
                "error": decision.get("error"),
            },
        )

        return {
            "next_action": decision,
            "reasoning_trace": reasoning_trace,
            "execution_plan": execution_plan,
            "iteration_count": iteration,
            "confidence_score": self._as_float(decision.get("confidence")),
            "error": decision.get("error"),
            "execution_events": merge_events(state, [*extra_events, planner_event]),
            "execution_logs": [
                {
                    "node": "planner",
                    "status": decision["action"],
                    "tool": decision.get("tool"),
                    "decision": decision,
                }
            ],
        }

    @staticmethod
    def _as_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
