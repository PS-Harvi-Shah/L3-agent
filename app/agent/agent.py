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

Your objective is to retrieve and consolidate complete product master data from the enterprise PostgreSQL database based on the user's request. You do this by writing read-only SQL and executing it through the available database tools.

You are the sole decision-maker. Reason about the user's request and decide:
1. What the user is asking for and which identifier they provided (product id, part number, product name, supplier id, or supplier name — part numbers can be numeric or alphanumeric).
2. Which tool to invoke and what SQL to run.
3. Whether additional queries are required.
4. When enough information has been collected to satisfy the user's request.

SQL rules:
- SELECT (or WITH) statements only — never modify data.
- Always schema-qualify tables (e.g. enterprise_data.products).
- Use ILIKE '%...%' for matching names (case-insensitive, partial).
- When returning products, JOIN the supplier so every product row includes its supplier's details.
- Follow the foreign keys in the schema to join related tables that add detail.
- Add LIMIT 25 to open-ended searches.
- If a query returns an error, read the error message and fix the SQL.

Interpreting identifiers:
- If the user's request NAMES the identifier type (e.g. "product id", "part number", "supplier id", "supplier name", "product name" — in any phrasing, including typos/abbreviations like "prod id" or "part no"), TRUST IT. Query only that one column with a single WHERE clause. Do NOT run the multi-interpretation OR checks below — there is nothing ambiguous to resolve.
- Only when the request gives a bare value with NO stated type, resolve ambiguity as follows:
  - A purely numeric value is ambiguous: it can be a product_id, a part_number, or a supplier_id. Check ALL THREE in ONE query using OR (see example below).
  - If that products query returns 0 rows, the number can STILL be a supplier with no products — check the suppliers table directly (SELECT * FROM enterprise_data.suppliers WHERE supplier_id = ...) before concluding that nothing exists.
  - A value containing letters is a part_number ONLY if it looks like a compact code (e.g. 'A18-4'). Anything containing a space (e.g. 'Nitric Acid') is NEVER a part_number — it is a name. A name can be a product name OR a supplier name — check BOTH in ONE query: WHERE p.product_name ILIKE '%...%' OR s.supplier_name ILIKE '%...%' (see example below).
- Repeating a query that already returned 0 rows will return 0 rows again — change the query instead.
- As soon as a query returns rows, that interpretation is the answer — stop trying others.

Example queries:
- Type stated ("product id 3731599") — single column, no OR:
  SELECT p.*, s.supplier_name FROM enterprise_data.products p JOIN enterprise_data.suppliers s ON s.supplier_id = p.supplier_id WHERE p.product_id = 3731599;
- Type stated ("part number 34860") — single column even though the value is numeric, because the type was named:
  SELECT p.*, s.supplier_name FROM enterprise_data.products p JOIN enterprise_data.suppliers s ON s.supplier_id = p.supplier_id WHERE p.part_number = '34860';
- Type stated ("supplier name Merck") — single column, matching supplier rows include their catalog:
  SELECT p.*, s.supplier_name FROM enterprise_data.products p JOIN enterprise_data.suppliers s ON s.supplier_id = p.supplier_id WHERE s.supplier_name ILIKE '%merck%' LIMIT 25;
- No type stated, bare number "557" — checked as product id, part number, and supplier id at once:
  SELECT p.*, s.supplier_name FROM enterprise_data.products p JOIN enterprise_data.suppliers s ON s.supplier_id = p.supplier_id WHERE p.product_id = 557 OR p.part_number = '557' OR p.supplier_id = 557;
- No type stated, bare text "Merck" — checked as product name and supplier name at once:
  SELECT p.*, s.supplier_name FROM enterprise_data.products p JOIN enterprise_data.suppliers s ON s.supplier_id = p.supplier_id WHERE p.product_name ILIKE '%merck%' OR s.supplier_name ILIKE '%merck%' LIMIT 25;

Answer scope:
- If the user asked about one product, report that product and its supplier — do NOT list the supplier's whole catalog.
- If the user asked about a supplier, include its product catalog.

Never invent or infer database records. Base every statement on retrieved rows only. If nothing matches after every plausible interpretation, clearly state that no matching data exists and suggest alternative identifiers the user could provide.

Rules:
- Call at most ONE tool per turn, then wait for its result before deciding the next action.
- To finish, reply in plain text WITHOUT calling a tool: a short summary of what was found and how the records relate.
"""

_DISCOVER_SCHEMA_NOTE = """
Database schema: unknown at startup. Before writing SQL, use the server's schema tools (e.g. list_schemas, list_objects, get_object_details) to discover the tables and their columns.
"""


class MasterDataAgent:
    """Runs one user query through the agentic loop and streams events."""

    def __init__(
        self, llm: LLMClient, toolbelt: ToolBelt, schema_summary: str | None = None
    ) -> None:
        settings = get_settings()
        self._llm = llm
        self._tools = toolbelt
        self._schema_summary = schema_summary
        self._max_steps = max(1, settings.agent_max_steps)
        self._deadline_seconds = settings.agent_deadline_seconds

    def _system_prompt(self) -> str:
        if self._schema_summary:
            return f"{_SYSTEM_PROMPT}\nDatabase schema:\n{self._schema_summary}\n"
        return f"{_SYSTEM_PROMPT}\n{_DISCOVER_SCHEMA_NOTE}"

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
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": f"User request: {query.strip()}"},
        ]

        events: list[dict[str, Any]] = []
        reasoning_trace: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        products: dict[int, dict[str, Any]] = {}
        suppliers: dict[int, dict[str, Any]] = {}
        records: list[dict[str, Any]] = []
        seen_calls: dict[str, ToolResult] = {}

        def has_data() -> bool:
            return bool(products or suppliers or records)

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
                status = "complete" if has_data() else "no_results"
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
                    self._absorb(result.data, products, suppliers, records)

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

        if answer is None and status == "incomplete" and has_data():
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
                "records": records,
            },
            "counts": {
                "products": len(products),
                "suppliers": len(suppliers),
                "records": len(records),
            },
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
        records: list[dict[str, Any]],
    ) -> None:
        """Bucket retrieved SQL rows by the identifying key they carry.

        Rows without an id column (the model may SELECT only names) still
        count as retrieved data — they land in the generic records bucket.
        """
        for row in data.get("rows", []):
            if not isinstance(row, dict):
                continue
            product_id = row.get("product_id")
            supplier_id = row.get("supplier_id")
            if product_id is not None:
                products[product_id] = row
            elif supplier_id is not None:
                suppliers[supplier_id] = row
            elif row not in records:
                records.append(row)
            if supplier_id is not None and row.get("supplier_name") is not None:
                suppliers.setdefault(
                    supplier_id,
                    {"supplier_id": supplier_id, "supplier_name": row["supplier_name"]},
                )

    @staticmethod
    def _render_observation(result: ToolResult, repeated: bool) -> str:
        if not result.success:
            return json.dumps({"error": result.error}, separators=(",", ":"))
        payload = dict(result.data)
        if result.record_count == 0:
            payload["note"] = (
                "0 rows. Other interpretations of the identifier may still match — "
                "try them before finishing."
            )
        if repeated:
            payload["note"] = (
                "You already ran this exact call. Try a different query "
                "or finish with your answer."
            )
        return json.dumps(payload, separators=(",", ":"), default=str)
