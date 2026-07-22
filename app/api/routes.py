import json
import logging
from typing import Annotated, Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.agent import MasterDataAgent, ToolBelt
from app.agent.audit import list_executions, load_execution, persist_execution
from app.llm import get_llm_client
from app.mcp_client import MCPClient
from app.schemas import AgentQueryRequest, AgentQueryResponse, ToolInfo


router = APIRouter(tags=["agent"])
logger = logging.getLogger("api")


def get_mcp_client(request: Request) -> MCPClient:
    return request.app.state.mcp_client


def get_agent(
    mcp: Annotated[MCPClient, Depends(get_mcp_client)],
) -> MasterDataAgent:
    toolbelt = ToolBelt(mcp)
    return MasterDataAgent(get_llm_client(), toolbelt, schema_summary=mcp.schema_summary)


@router.get("/tools", response_model=list[ToolInfo])
def list_tools(mcp: Annotated[MCPClient, Depends(get_mcp_client)]) -> list[ToolInfo]:
    """The tool catalog discovered from the MCP server and exposed to the agent."""
    return [ToolInfo(**t) for t in ToolBelt(mcp).catalog()]


@router.post("/agent/query", response_model=AgentQueryResponse)
def agent_query(
    request: AgentQueryRequest,
    agent: Annotated[MasterDataAgent, Depends(get_agent)],
) -> AgentQueryResponse:
    """Run the agent to completion and return the consolidated result."""
    result = agent.run(request.query)
    persist_execution(result)
    return AgentQueryResponse(**result)


@router.post("/agent/query/stream")
def agent_query_stream(
    request: AgentQueryRequest,
    agent: Annotated[MasterDataAgent, Depends(get_agent)],
) -> StreamingResponse:
    """Run the agent and stream execution events live (SSE)."""

    def event_generator() -> Iterator[str]:
        try:
            for item in agent.run_stream(request.query):
                if item["type"] == "event":
                    payload = {"type": "event", "event": item["event"]}
                else:
                    persist_execution(item["result"])
                    payload = {"type": "complete", "response": item["result"]}
                yield f"data: {json.dumps(payload, default=str)}\n\n"
        except Exception as exc:  # never leave the SSE stream hanging
            logger.exception("Agent stream failed")
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/agent/history")
def agent_history(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """Summaries of past agent executions (audit trail)."""
    return list_executions(limit=limit, offset=offset)


@router.get("/agent/execution/{execution_id}")
def agent_execution(execution_id: str) -> dict[str, Any]:
    """Full persisted trace of one agent execution."""
    trace = load_execution(execution_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Execution {execution_id} not found")
    return trace
