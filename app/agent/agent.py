"""The Master Data Discovery Agent.

A ReAct-style loop where the LLM is the only decision-maker:

    reason (LLM + tool catalog) -> act (execute chosen tool) -> observe -> repeat

The application code around it is a pure execution harness. It never
interprets the user's identifier, never picks a tool, and never judges
completeness — it only executes what the agent decided, records what
happened, and enforces hard safety bounds (step budget and deadline).
"""

import json
import logging
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.llm import AssistantTurn, LLMClient, LLMError
from app.agent.tools import ToolBelt, ToolResult


logger = logging.getLogger("agent")


_SYSTEM_PROMPT = """
You are the Planner for a Master Data Discovery Agent.

Your objective is to retrieve and consolidate complete product master data from the available enterprise SQL databases based on a single user-provided identifier.

Available data:
- products(product_id, product_name, supplier_id, part_number, language, country)
- suppliers(supplier_id, supplier_name)

The user may provide any one of the following identifiers:
- Product ID
- Product Name
- Supplier ID
- Supplier Name
- Part Number (numeric or alphanumeric)

You are the sole decision-maker. Reason about the user's input and decide:
1. What type of identifier the user most likely provided.
2. Which tool should be invoked.
3. What search strategy should be used.
4. Whether additional retrieval steps are required.
5. When enough information has been collected to satisfy the user's request.

Do not assume the first interpretation is correct. If search_master_data returns 0 records, call it again with the next plausible identifier_type (a bare number can be a product_id, a part_number, or a supplier_id; text can be a product_name or a supplier_name). Only conclude that nothing exists after every plausible identifier_type has been tried. But as soon as ONE search returns records, that interpretation is the answer — stop trying other interpretations.

Answer scope depends on what the user's identifier resolves to:
- PRODUCT (identifier_type product_id, part_number, or product_name): the search result already includes the product AND its supplier details. Finish immediately — do NOT list the supplier's other products; the user asked about one product, not the supplier's catalog.
- SUPPLIER (identifier_type supplier_id or supplier_name): call get_products_of_supplier once to list its catalog, then finish.

Never invent or infer database records. Base every decision solely on the retrieved data. If no matching records are found after exhausting all reasonable search strategies, clearly state that no matching data exists and suggest alternative identifiers the user could provide.

Your responsibility is to plan and decide the next action. Every decision should be based on reasoning rather than assumptions.

Rules:
- Call at most ONE tool per turn, then wait for its result before deciding the next action.
- Use only IDs that appear in records already retrieved — never guess an ID.
- To finish, reply in plain text WITHOUT calling a tool: a short summary of what was found and how the records relate.
"""


class MasterDataAgent:
    """Runs one user query through the agentic loop and streams events."""

    def __init__(self, llm: LLMClient, toolbelt: ToolBelt) -> None:
        settings = get_settings()
        self._llm = llm
        self._tools = toolbelt
        self._max_steps = max(1, settings.agent_max_steps)
        self._deadline_seconds = settings.agent_deadline_seconds

    # -- public API -----------------------------------------------------------

    def run(self, query: str) -> dict[str, Any]:
        """Execute the loop to completion and return the final result."""
        final: dict[str, Any] = {}
        for event in self.run_stream(query):
            if event.get("type") == "final":
                final = event["result"]
        return final

    def run_stream(self, query: str) -> Iterator[dict[str, Any]]:
        """Execute the loop, yielding events as they happen.

        Yields ``{"type": "event", "event": {...}}`` for each execution event
        and a terminal ``{"type": "final", "result": {...}}``.
        """
        execution_id = uuid.uuid4().hex[:16]
        started = time.perf_counter()
        log = logging.LoggerAdapter(logger, {"execution_id": execution_id})
        log.info("Agent run started", extra={"query": query, "model": self._llm.model})

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Identifier: {query.strip()}"},
        ]

        events: list[dict[str, Any]] = []
        reasoning_trace: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        products: dict[int, dict[str, Any]] = {}
        suppliers: dict[int, dict[str, Any]] = {}
        seen_calls: dict[str, ToolResult] = {}

        def emit(node: str, phase: str, message: str, data: dict[str, Any] | None = None):
            event = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "node": node,
                "phase": phase,
                "message": message,
                "data": data or {},
            }
            events.append(event)
            return {"type": "event", "event": event}

        yield emit(
            "agent",
            "started",
            f"Agent started for query '{query.strip()}' with {len(self._tools.catalog())} tools",
            {"model": self._llm.model, "tools": [t["name"] for t in self._tools.catalog()]},
        )

        status = "incomplete"
        answer: str | None = None
        error: str | None = None
        nudged = False

        step = 0
        while step < self._max_steps:
            step += 1
            if time.perf_counter() - started > self._deadline_seconds:
                error = f"Deadline of {self._deadline_seconds:.0f}s exceeded"
                log.warning("Agent deadline exceeded", extra={"step": step})
                yield emit("agent", "deadline", error)
                break

            try:
                turn = self._llm.chat(messages, tools=self._tools.specs())
            except LLMError as exc:
                status = "error"
                error = str(exc)
                log.error("Agent reasoning failed", extra={"step": step, "error": error})
                yield emit("agent", "error", f"Reasoning model failed: {error}")
                break

            reasoning_trace.append(
                {
                    "step": step,
                    "thought": turn.content or None,
                    "action": "call_tool" if turn.tool_calls else "finish",
                    "tool": turn.tool_calls[0].name if turn.tool_calls else None,
                    "tool_input": turn.tool_calls[0].arguments if turn.tool_calls else None,
                    "llm_time_ms": turn.latency_ms,
                }
            )

            if not turn.tool_calls:
                if not turn.content.strip() and not nudged:
                    # Transport hiccup (empty reply) — give the agent one chance
                    # to answer; this adds no decision of our own.
                    nudged = True
                    messages.append(
                        {
                            "role": "user",
                            "content": "Your reply was empty. Either call a tool or state your final answer.",
                        }
                    )
                    yield emit("agent", "retry", "Model returned an empty reply; asking it to continue")
                    continue

                answer = turn.content.strip()
                status = "complete" if (products or suppliers) else "no_results"
                log.info(
                    "Agent finished",
                    extra={"step": step, "status": status, "llm_time_ms": turn.latency_ms},
                )
                yield emit(
                    "agent",
                    "finished",
                    answer or "Agent finished without an answer",
                    {"step": step, "status": status},
                )
                break

            messages.append(turn.to_message())
            yield emit(
                "agent",
                "decision",
                self._describe_decision(turn),
                {
                    "step": step,
                    "thought": turn.content or None,
                    "tool_calls": [
                        {"tool": c.name, "arguments": c.arguments} for c in turn.tool_calls
                    ],
                    "llm_time_ms": turn.latency_ms,
                },
            )

            for call in turn.tool_calls:
                call_key = json.dumps({"tool": call.name, "args": call.arguments}, sort_keys=True)
                repeated = call_key in seen_calls

                yield emit(
                    "tool",
                    "started",
                    f"Executing {call.name}({json.dumps(call.arguments)})",
                    {"tool": call.name, "arguments": call.arguments},
                )

                if repeated:
                    result = seen_calls[call_key]
                    log.info("Repeated tool call served from cache", extra={"tool": call.name})
                else:
                    result = self._tools.execute(call.name, call.arguments)
                    seen_calls[call_key] = result

                if result.success:
                    self._absorb(result.data, products, suppliers)

                tool_calls.append(
                    {
                        "step": step,
                        "tool": call.name,
                        "tool_input": call.arguments,
                        "success": result.success,
                        "record_count": result.record_count,
                        "error": result.error,
                        "execution_time_ms": result.execution_time_ms,
                    }
                )

                observation = self._render_observation(result, repeated)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": observation})

                yield emit(
                    "tool",
                    "completed" if result.success else "error",
                    (
                        f"{call.name} returned {result.record_count} record(s) in {result.execution_time_ms}ms"
                        if result.success
                        else f"{call.name} failed: {result.error}"
                    ),
                    {
                        "tool": call.name,
                        "success": result.success,
                        "record_count": result.record_count,
                        "execution_time_ms": result.execution_time_ms,
                        "error": result.error,
                    },
                )
        else:
            error = f"Step budget of {self._max_steps} exhausted before the agent finished"
            log.warning("Agent step budget exhausted")
            yield emit("agent", "budget_exhausted", error)

        if answer is None and status == "incomplete" and (products or suppliers):
            # The loop was cut off after data was already retrieved. Give the
            # agent one last turn, without tools, to summarize its findings —
            # the summary is still the model's own, not ours.
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Stop searching. Summarize what you found from the tool "
                        "results above in plain text."
                    ),
                }
            )
            try:
                closing = self._llm.chat(messages)
                if closing.content.strip():
                    answer = closing.content.strip()
                    reasoning_trace.append(
                        {
                            "step": len(reasoning_trace) + 1,
                            "thought": None,
                            "action": "forced_finish",
                            "tool": None,
                            "tool_input": None,
                            "llm_time_ms": closing.latency_ms,
                        }
                    )
                    yield emit(
                        "agent",
                        "finished",
                        answer,
                        {"status": status, "forced": True},
                    )
            except LLMError as exc:
                log.error("Forced finalization failed", extra={"error": str(exc)})

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        result = {
            "execution_id": execution_id,
            "query": query,
            "status": status,
            "answer": answer,
            "consolidated_data": {
                "products": list(products.values()),
                "suppliers": list(suppliers.values()),
            },
            "counts": {"products": len(products), "suppliers": len(suppliers)},
            "reasoning_trace": reasoning_trace,
            "tool_calls": tool_calls,
            "execution_events": events,
            "duration_ms": duration_ms,
            "error": error,
        }
        log.info(
            "Agent run completed",
            extra={
                "status": status,
                "duration_ms": duration_ms,
                "steps": len(reasoning_trace),
                "tool_calls": len(tool_calls),
                "products": len(products),
                "suppliers": len(suppliers),
            },
        )
        yield {"type": "final", "result": result}

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _describe_decision(turn: AssistantTurn) -> str:
        calls = ", ".join(
            f"{c.name}({json.dumps(c.arguments)})" for c in turn.tool_calls
        )
        if turn.content.strip():
            return f"{turn.content.strip()} -> {calls}"
        return f"Agent decided to call: {calls}"

    @staticmethod
    def _absorb(
        data: dict[str, Any],
        products: dict[int, dict[str, Any]],
        suppliers: dict[int, dict[str, Any]],
    ) -> None:
        for record in data.get("products", []):
            key = record.get("product_id")
            if key is not None:
                products[key] = record
        for record in data.get("suppliers", []):
            key = record.get("supplier_id")
            if key is not None:
                suppliers[key] = record

    @staticmethod
    def _render_observation(result: ToolResult, repeated: bool) -> str:
        if not result.success:
            return json.dumps({"error": result.error}, separators=(",", ":"))
        payload = dict(result.data)
        if result.record_count == 0:
            payload["note"] = (
                "0 records with this identifier_type. Other identifier_type values "
                "may still match — try them before finishing."
            )
        if repeated:
            payload["note"] = (
                "You already called this tool with these arguments. Try a different "
                "interpretation or finish with your answer."
            )
        return json.dumps(payload, separators=(",", ":"), default=str)
