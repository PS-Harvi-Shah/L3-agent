import json
import sys
from pathlib import Path
from typing import Any, Iterator

import requests
import streamlit as st
from requests import RequestException

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings


settings = get_settings()
API_BASE = settings.api_base_url.rstrip("/")

_STATUS_BADGES = {
    "complete": ("Complete", "green"),
    "no_results": ("No results", "orange"),
    "incomplete": ("Incomplete", "orange"),
    "error": ("Error", "red"),
}

_EVENT_ICONS = {
    ("agent", "started"): "🚀",
    ("agent", "decision"): "🧠",
    ("agent", "finished"): "🏁",
    ("agent", "retry"): "🔄",
    ("agent", "error"): "❌",
    ("agent", "deadline"): "⏰",
    ("agent", "budget_exhausted"): "⏰",
    ("tool", "started"): "🔧",
    ("tool", "completed"): "✅",
    ("tool", "error"): "⚠️",
}


def stream_agent_query(query: str) -> Iterator[dict[str, Any]]:
    url = f"{API_BASE}/agent/query/stream"
    with requests.post(url, json={"query": query}, stream=True, timeout=600) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            yield json.loads(line[6:])


def fetch_history(limit: int = 25) -> list[dict[str, Any]]:
    response = requests.get(f"{API_BASE}/agent/history", params={"limit": limit}, timeout=10)
    response.raise_for_status()
    return response.json()


def event_line(event: dict[str, Any]) -> str:
    node = event.get("node", "")
    phase = event.get("phase", "")
    message = str(event.get("message", ""))
    icon = _EVENT_ICONS.get((node, phase), "•")
    timestamp = str(event.get("timestamp", ""))[11:19]
    return f"{icon} `{timestamp}` **{node} · {phase}** — {message}"


def render_events(container, events: list[dict[str, Any]]) -> None:
    with container:
        for event in events:
            st.markdown(event_line(event))
            data = event.get("data") or {}
            llm_ms = data.get("llm_time_ms")
            tool_ms = data.get("execution_time_ms")
            details = []
            if llm_ms:
                details.append(f"reasoning {llm_ms / 1000:.1f}s")
            if tool_ms is not None:
                details.append(f"query {tool_ms:.0f}ms")
            if details:
                st.caption(" · ".join(details))


def render_response(response: dict[str, Any]) -> None:
    status = response.get("status", "unknown")
    label, color = _STATUS_BADGES.get(status, (status, "gray"))
    answer = response.get("answer")
    error = response.get("error")
    counts = response.get("counts", {})

    st.markdown(f"**Status:** :{color}[{label}]")
    if error:
        st.error(error)
    if answer:
        (st.success if status == "complete" else st.warning)(answer)

    col1, col2, col3 = st.columns(3)
    col1.metric("Products", counts.get("products", 0))
    col2.metric("Suppliers", counts.get("suppliers", 0))
    col3.metric("Duration", f"{response.get('duration_ms', 0) / 1000:.1f}s")

    data = response.get("consolidated_data", {})
    products = data.get("products", [])
    suppliers = data.get("suppliers", [])
    records = data.get("records", [])
    if products:
        st.markdown("##### Products")
        st.dataframe(products, use_container_width=True, hide_index=True)
    if suppliers:
        st.markdown("##### Suppliers")
        st.dataframe(suppliers, use_container_width=True, hide_index=True)
    if records:
        st.markdown("##### Other records")
        st.dataframe(records, use_container_width=True, hide_index=True)
    if not products and not suppliers and not records:
        st.info("No records were retrieved for this identifier.")

    trace = response.get("reasoning_trace", [])
    calls = response.get("tool_calls", [])
    with st.expander(f"🧠 Reasoning trace ({len(trace)} steps)"):
        for entry in trace:
            action = entry.get("action", "")
            tool = entry.get("tool")
            thought = entry.get("thought")
            line = f"**Step {entry.get('step')}** · `{action}`"
            if tool:
                line += f" → `{tool}({json.dumps(entry.get('tool_input') or {})})`"
            st.markdown(line)
            if thought:
                st.caption(thought)
    with st.expander(f"🔧 Tool calls ({len(calls)})"):
        if calls:
            st.dataframe(
                [
                    {
                        "step": c.get("step"),
                        "tool": c.get("tool"),
                        "input": json.dumps(c.get("tool_input") or {}),
                        "records": c.get("record_count"),
                        "ok": c.get("success"),
                        "time (ms)": c.get("execution_time_ms"),
                        "error": c.get("error"),
                    }
                    for c in calls
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("No tools were called.")
    st.caption(f"Execution ID: `{response.get('execution_id', '')}`")


def render_agent_tab() -> None:
    st.caption(
        "Ask about a product or supplier. State the identifier type explicitly "
        "(product id, part number, product name, supplier id, or supplier name) "
        "for the fastest, most reliable lookup — the agent still handles bare "
        "values, but naming the type removes all guesswork."
    )

    with st.form("query_form", border=False):
        col1, col2 = st.columns([5, 1])
        query = col1.text_input(
            "Identifier",
            placeholder="e.g. product id 3731599, part number A18-4, supplier name Merck",
            label_visibility="collapsed",
        )
        search = col2.form_submit_button("🔍 Search", type="primary", use_container_width=True)

    if not (search and query.strip()):
        return

    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("Live execution")
        events_area = st.empty()
    with right:
        st.subheader("Result")
        result_area = st.container()

    events: list[dict[str, Any]] = []
    final_response: dict[str, Any] | None = None
    error_detail: str | None = None

    try:
        with st.spinner("Agent reasoning — each step takes a few seconds on a local model..."):
            for chunk in stream_agent_query(query.strip()):
                kind = chunk.get("type")
                if kind == "event":
                    events.append(chunk.get("event", {}))
                    render_events(events_area.container(), events)
                elif kind == "complete":
                    final_response = chunk.get("response")
                elif kind == "error":
                    error_detail = chunk.get("detail", "Agent execution failed")
    except RequestException as exc:
        error_detail = (
            f"Could not reach the agent API at {API_BASE}. "
            f"Start it with `uvicorn app.main:app`. ({exc})"
        )

    with result_area:
        if error_detail:
            st.error(error_detail)
        elif final_response:
            render_response(final_response)
        else:
            st.info("No response received from the agent.")


def render_history_tab() -> None:
    st.caption("Every agent run is persisted with its full reasoning trace and tool calls.")
    try:
        history = fetch_history()
    except RequestException as exc:
        st.error(f"Could not load history from the agent API: {exc}")
        return

    if not history:
        st.info("No executions recorded yet. Run a query on the Agent tab.")
        return

    st.dataframe(
        [
            {
                "execution_id": h.get("execution_id"),
                "query": h.get("query"),
                "status": h.get("status"),
                "duration (s)": round((h.get("duration_ms") or 0) / 1000, 1),
                "steps": h.get("steps"),
                "tool calls": h.get("tool_calls"),
                "products": (h.get("counts") or {}).get("products", 0),
                "suppliers": (h.get("counts") or {}).get("suppliers", 0),
                "error": h.get("error"),
            }
            for h in history
        ],
        use_container_width=True,
        hide_index=True,
    )

    execution_id = st.selectbox(
        "Inspect an execution",
        [h.get("execution_id") for h in history],
        index=None,
        placeholder="Select an execution id to see its full trace",
    )
    if execution_id:
        try:
            response = requests.get(f"{API_BASE}/agent/execution/{execution_id}", timeout=10)
            response.raise_for_status()
            render_response(response.json())
        except RequestException as exc:
            st.error(f"Could not load execution {execution_id}: {exc}")


def main() -> None:
    st.set_page_config(
        page_title="Master Data Discovery Agent",
        page_icon="🔍",
        layout="wide",
    )
    st.title("🔍 Master Data Discovery Agent")

    agent_tab, history_tab = st.tabs(["Agent", "History"])
    with agent_tab:
        render_agent_tab()
    with history_tab:
        render_history_tab()


if __name__ == "__main__":
    main()
