"""Measure how reliably the agent turns an identifier into correct SQL.

Two regimes are tested:

- BARE cases: a value with no stated type (e.g. "557") — the agent must
  resolve ambiguity itself (numeric -> check product_id/part_number/
  supplier_id; text -> check product_name/supplier_name).
- LABELED cases: the query names the identifier type explicitly (e.g.
  "supplier id 557") — the agent should trust it and go straight to a single
  query on that one column, with NO ambiguity-resolution retries. This is the
  now-expected usage pattern; a labeled case taking more than one query is a
  regression (the model ignored the stated type).

For each case the agent runs end-to-end against the MCP server; we record
whether its FIRST SQL query filters on the correct column, how many SQL
calls it needed, and whether the data was found at all. Run before/after a
prompt or model change to compare.

Usage: python -m scripts.eval_classification
Requires the local Postgres `master_data` DB, the Postgres MCP server
(spawned automatically over stdio), and Ollama to be running.
"""

import time

from app.agent import MasterDataAgent, ToolBelt
from app.llm import get_llm_client
from app.mcp_client import MCPClient


# (input, column the first SQL should filter on) — values from scripts/init_db.sql
BARE_TEST_CASES: list[tuple[str, str]] = [
    ("3731599", "product_id"),        # Acetone
    ("3731605", "product_id"),        # Nitric Acid
    ("303440250", "part_number"),     # numeric part number
    ("34860", "part_number"),         # numeric part number (Methanol)
    ("A18-4", "part_number"),         # alphanumeric part number
    ("H325", "part_number"),          # alphanumeric part number
    ("557", "supplier_id"),           # Merck
    ("560", "supplier_id"),           # Honeywell Research Chemicals
    ("Acetone", "product_name"),
    ("Sulfuric Acid", "product_name"),
    ("Merck", "supplier_name"),
    ("Fisher Scientific", "supplier_name"),
]

# Type explicitly named in the query — should resolve in exactly ONE query,
# no OR-based multi-interpretation fallback, even for numeric part/supplier ids.
LABELED_TEST_CASES: list[tuple[str, str]] = [
    ("product id 3731599", "product_id"),
    ("product id 3731605", "product_id"),
    ("part number 303440250", "part_number"),   # numeric part number, type stated
    ("part number 34860", "part_number"),       # numeric part number, type stated
    ("part number A18-4", "part_number"),
    ("part number H325", "part_number"),
    ("supplier id 557", "supplier_id"),         # numeric, type stated — was ambiguous bare
    ("supplier id 560", "supplier_id"),
    ("product name Acetone", "product_name"),
    ("product name Sulfuric Acid", "product_name"),
    ("supplier name Merck", "supplier_name"),
    ("supplier name Fisher Scientific", "supplier_name"),
]


def _sql_calls(result: dict) -> list[str]:
    statements = []
    for call in result["tool_calls"]:
        for key, value in (call.get("tool_input") or {}).items():
            if key.lower() in ("sql", "query", "statement") and isinstance(value, str):
                statements.append(value)
    return statements


def _run_case(agent_factory, value: str, expected_column: str, require_single_query: bool) -> dict:
    agent = agent_factory()
    started = time.perf_counter()
    result = agent.run(value)
    duration = time.perf_counter() - started

    statements = _sql_calls(result)
    first_sql = statements[0] if statements else ""
    column_hit = expected_column.lower() in first_sql.lower()
    single_query = len(statements) == 1
    hit = column_hit and (single_query if require_single_query else True)
    found = result["status"] == "complete"

    label = "HIT " if hit else "MISS"
    print(
        f"{value!r:>28}  expect={expected_column:<13} {label}  "
        f"queries={len(statements)}  status={result['status']}  {duration:.1f}s"
    )
    if first_sql:
        print(f"{'':>30}first SQL: {first_sql[:120]}")
    if require_single_query and column_hit and not single_query:
        print(f"{'':>30}(column correct, but took {len(statements)} queries — should be 1)")

    return {"hit": hit, "found": found, "queries": len(statements)}


def _summarize(label: str, rows: list[dict]) -> None:
    hits = sum(1 for r in rows if r["hit"])
    found = sum(1 for r in rows if r["found"])
    total_queries = sum(r["queries"] for r in rows)
    print("-" * 80)
    print(f"[{label}] correct: {hits}/{len(rows)} ({hits / len(rows):.0%})")
    print(f"[{label}] data found: {found}/{len(rows)}")
    print(
        f"[{label}] total SQL calls: {total_queries} "
        f"(avg {total_queries / len(rows):.2f} per query; 1.00 is ideal)"
    )


def run_eval() -> None:
    llm = get_llm_client()
    mcp = MCPClient()
    mcp.connect()
    try:
        def make_agent() -> MasterDataAgent:
            return MasterDataAgent(llm, ToolBelt(mcp), schema_summary=mcp.schema_summary)

        print("=== BARE identifiers (no stated type — ambiguity resolution) ===")
        bare_rows = [
            _run_case(make_agent, value, column, require_single_query=False)
            for value, column in BARE_TEST_CASES
        ]
        _summarize("BARE", bare_rows)

        print()
        print("=== LABELED identifiers (type stated — expect single-query resolution) ===")
        labeled_rows = [
            _run_case(make_agent, value, column, require_single_query=True)
            for value, column in LABELED_TEST_CASES
        ]
        _summarize("LABELED", labeled_rows)
    finally:
        mcp.close()


if __name__ == "__main__":
    run_eval()
