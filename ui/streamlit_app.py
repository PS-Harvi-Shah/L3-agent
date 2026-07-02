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


def stream_agent_query(query: str) -> Iterator[dict[str, Any]]:
    url = f"{settings.api_base_url.rstrip('/')}/agent/query/stream"
    with requests.post(url, json={"query": query}, stream=True, timeout=120) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            payload = json.loads(line[6:])
            yield payload


def render_execution_event(event: dict[str, Any]) -> None:
    node = event.get("node", "unknown")
    phase = event.get("phase", "")
    message = event.get("message", "")
    timestamp = event.get("timestamp", "")
    data = event.get("data") or {}

    header = f"**{node}** · `{phase}`"
    if timestamp:
        header = f"{header} · {timestamp}"

    st.markdown(header)
    st.markdown(message)
    if data:
        st.json(data)
    st.divider()


def render_final_response(response: dict[str, Any]) -> None:
    st.subheader("Response")
    st.json(response)


def main() -> None:
    st.set_page_config(
        page_title="Master Data Discovery Agent",
        page_icon=":material/search:",
        layout="wide",
    )
    st.title("Master Data Discovery Agent")
    st.caption("Enter any product or supplier identifier to run the agentic discovery workflow.")

    query = st.text_input(
        "Identifier",
        placeholder="Product ID, name, part number, supplier ID, or supplier name",
        label_visibility="collapsed",
    )

    col1, _ = st.columns([1, 5])
    with col1:
        search = st.button("Search", type="primary", disabled=not query.strip())

    execution_panel = st.container(border=True)
    response_panel = st.container(border=True)

    if search and query.strip():
        events: list[dict[str, Any]] = []
        final_response: dict[str, Any] | None = None
        error_detail: str | None = None

        with execution_panel:
            st.subheader("Agent Execution")
            event_placeholder = st.empty()
            status_placeholder = st.empty()

            try:
                for chunk in stream_agent_query(query.strip()):
                    chunk_type = chunk.get("type")

                    if chunk_type == "node_update":
                        events = chunk.get("execution_events", events)
                        with event_placeholder.container():
                            for event in events:
                                render_execution_event(event)
                        node = chunk.get("node", "")
                        status_placeholder.caption(f"Running: {node}")

                    elif chunk_type == "complete":
                        final_response = chunk.get("response")
                        duration = chunk.get("duration_ms", 0)
                        status_placeholder.caption(f"Completed in {duration:.0f}ms")

                    elif chunk_type == "error":
                        error_detail = chunk.get("detail", "Agent execution failed")

            except RequestException as exc:
                error_detail = str(exc)

        if error_detail:
            with response_panel:
                st.error(error_detail)
        elif final_response:
            with response_panel:
                render_final_response(final_response)
        else:
            with response_panel:
                st.info("No response received from agent.")


if __name__ == "__main__":
    main()
