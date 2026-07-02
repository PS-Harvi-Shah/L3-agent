import json
import time
from typing import Annotated, Iterator, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database.connection import get_db_session
from app.repositories.exceptions import DataAccessError
from app.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    LookupResult,
    ProductRead,
    RetrievalRequest,
    RetrievalResponse,
    SupplierRead,
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolInfo,
)
from app.agent.graph import AgentGraph
from app.mcp.server import MCPServer
from app.observability.router import trace_agent_execution
from app.services import DataAccessService, RetrievalEngine


router = APIRouter(tags=["data-access"])
SchemaT = TypeVar("SchemaT")


def get_data_access_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> DataAccessService:
    return DataAccessService(session)


def get_retrieval_engine(
    session: Annotated[Session, Depends(get_db_session)],
) -> RetrievalEngine:
    return RetrievalEngine(session)


def get_mcp_server(
    session: Annotated[Session, Depends(get_db_session)],
) -> MCPServer:
    return MCPServer(session)

def get_agent_graph(
    session: Annotated[Session, Depends(get_db_session)],
) -> AgentGraph:
    return AgentGraph(session)


def require_record(record: SchemaT | None, entity_name: str) -> SchemaT:
    if record is None:
        raise HTTPException(status_code=404, detail=f"{entity_name} not found")
    return record


def handle_data_access_error(exc: DataAccessError):
    raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/lookup", response_model=LookupResult)
def lookup(
    service: Annotated[DataAccessService, Depends(get_data_access_service)],
    entity_type: Annotated[str, Query(min_length=1)],
    identifier_type: Annotated[str, Query(min_length=1)],
    value: Annotated[str, Query(min_length=1)],
) -> LookupResult:
    try:
        return service.lookup(entity_type, identifier_type, value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DataAccessError as exc:
        handle_data_access_error(exc)


@router.post("/lookup", response_model=RetrievalResponse)
def intelligent_lookup(
    request: RetrievalRequest,
    retrieval_engine: Annotated[RetrievalEngine, Depends(get_retrieval_engine)],
) -> RetrievalResponse:
    try:
        return retrieval_engine.retrieve_from_query(request.query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DataAccessError as exc:
        handle_data_access_error(exc)


@router.get("/products/by-name/{product_name}", response_model=list[ProductRead])
def get_products_by_name(
    product_name: str,
    service: Annotated[DataAccessService, Depends(get_data_access_service)],
) -> list[ProductRead]:
    try:
        return service.repo.get_by_product_name(product_name)
    except DataAccessError as exc:
        handle_data_access_error(exc)


@router.get("/products/by-sku/{sku}", response_model=ProductRead)
def get_product_by_sku(
    sku: str,
    service: Annotated[DataAccessService, Depends(get_data_access_service)],
) -> ProductRead:
    try:
        return require_record(service.repo.get_by_sku(sku), "Product")
    except DataAccessError as exc:
        handle_data_access_error(exc)


@router.get("/products/by-part-number/{part_number}", response_model=ProductRead)
def get_product_by_part_number(
    part_number: str,
    service: Annotated[DataAccessService, Depends(get_data_access_service)],
) -> ProductRead:
    try:
        return require_record(service.repo.get_by_part_number(part_number), "Product")
    except DataAccessError as exc:
        handle_data_access_error(exc)


@router.get("/products/by-supplier/{supplier_id}", response_model=list[ProductRead])
def get_products_by_supplier(
    supplier_id: int,
    service: Annotated[DataAccessService, Depends(get_data_access_service)],
) -> list[ProductRead]:
    try:
        return service.repo.get_by_supplier_id(supplier_id)
    except DataAccessError as exc:
        handle_data_access_error(exc)


@router.get("/products/{product_id}", response_model=ProductRead)
def get_product(
    product_id: int,
    service: Annotated[DataAccessService, Depends(get_data_access_service)],
) -> ProductRead:
    try:
        return require_record(service.repo.get_by_product_id(product_id), "Product")
    except DataAccessError as exc:
        handle_data_access_error(exc)


@router.get("/products/{product_id}/supplier", response_model=SupplierRead)
def get_product_supplier(
    product_id: int,
    service: Annotated[DataAccessService, Depends(get_data_access_service)],
) -> SupplierRead:
    try:
        return require_record(service.repo.get_supplier(product_id), "Supplier")
    except DataAccessError as exc:
        handle_data_access_error(exc)


@router.get("/suppliers/by-name/{supplier_name}", response_model=list[SupplierRead])
def get_suppliers_by_name(
    supplier_name: str,
    service: Annotated[DataAccessService, Depends(get_data_access_service)],
) -> list[SupplierRead]:
    try:
        return service.repo.get_supplier_by_name(supplier_name)
    except DataAccessError as exc:
        handle_data_access_error(exc)


@router.get("/suppliers/by-code/{supplier_code}", response_model=SupplierRead)
def get_supplier_by_code(
    supplier_code: str,
    service: Annotated[DataAccessService, Depends(get_data_access_service)],
) -> SupplierRead:
    try:
        return require_record(service.repo.get_by_code(supplier_code), "Supplier")
    except DataAccessError as exc:
        handle_data_access_error(exc)


@router.get("/suppliers/{supplier_id}", response_model=SupplierRead)
def get_supplier(
    supplier_id: int,
    service: Annotated[DataAccessService, Depends(get_data_access_service)],
) -> SupplierRead:
    try:
        return require_record(service.repo.get_supplier_by_id(supplier_id), "Supplier")
    except DataAccessError as exc:
        handle_data_access_error(exc)


@router.get("/suppliers/{supplier_id}/products", response_model=list[ProductRead])
def get_supplier_products(
    supplier_id: int,
    service: Annotated[DataAccessService, Depends(get_data_access_service)],
) -> list[ProductRead]:
    try:
        return service.repo.get_products(supplier_id)
    except DataAccessError as exc:
        handle_data_access_error(exc)


@router.get("/tools", response_model=list[ToolInfo])
def list_tools(
    mcp: Annotated[MCPServer, Depends(get_mcp_server)],
) -> list[ToolInfo]:
    return [ToolInfo(**t) for t in mcp.list_tools()]


@router.post("/tools/execute", response_model=ToolExecuteResponse)
def execute_tool(
    request: ToolExecuteRequest,
    mcp: Annotated[MCPServer, Depends(get_mcp_server)],
) -> ToolExecuteResponse:
    try:
        result = mcp.execute_tool(request.tool, dict(request.tool_input))
        return ToolExecuteResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DataAccessError as exc:
        handle_data_access_error(exc)


@router.post("/agent/query", response_model=AgentQueryResponse)
def agent_query(
    request: AgentQueryRequest,
    agent: Annotated[AgentGraph, Depends(get_agent_graph)],
) -> AgentQueryResponse:
    start_time = time.perf_counter()
    try:
        result = agent.invoke(request.query)
        trace_agent_execution(request.query, result, start_time)
        return _build_agent_response(request.query, result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DataAccessError as exc:
        handle_data_access_error(exc)


def _build_agent_response(query: str, result: dict) -> AgentQueryResponse:
    final = result.get("final_response") or {}
    return AgentQueryResponse(
        query=final.get("query", query),
        status=final.get("status", "unknown"),
        answer=final.get("answer"),
        clarification=final.get("clarification"),
        assessment=final.get("assessment"),
        confidence_score=final.get("confidence_score", 0.0),
        consolidated_data=final.get("consolidated_data", {}),
        counts=final.get("counts", {}),
        reasoning_trace=final.get("reasoning_trace", []),
        tool_calls=final.get("tool_calls", []),
        executed_tools=final.get("executed_tools", []),
        execution_plan=final.get("execution_plan", []),
        execution_timeline=final.get("execution_timeline", []),
        execution_events=final.get("execution_events", result.get("execution_events", [])),
        error=final.get("error") or result.get("error"),
    )


@router.post("/agent/query/stream")
def agent_query_stream(
    request: AgentQueryRequest,
    agent: Annotated[AgentGraph, Depends(get_agent_graph)],
) -> StreamingResponse:
    start_time = time.perf_counter()

    def event_generator() -> Iterator[str]:
        accumulated_events: list[dict] = []
        final_result: dict = {}

        try:
            for chunk in agent.stream(request.query):
                node = chunk.get("node", "")
                new_events = chunk.get("execution_events", [])
                if new_events:
                    accumulated_events = new_events

                if chunk.get("state"):
                    final_result = chunk["state"]
                elif chunk.get("final_response"):
                    final_result = {
                        "final_response": chunk.get("final_response"),
                        "execution_events": accumulated_events,
                    }

                payload = {
                    "type": "node_update",
                    "node": node,
                    "execution_events": accumulated_events,
                }
                yield f"data: {json.dumps(payload, default=str)}\n\n"

            end_time = time.perf_counter()
            if final_result:
                trace_agent_execution(request.query, final_result, start_time)
            response = _build_agent_response(request.query, final_result)
            done_payload = {
                "type": "complete",
                "response": response.model_dump(),
                "duration_ms": round((end_time - start_time) * 1000, 2),
            }
            yield f"data: {json.dumps(done_payload, default=str)}\n\n"
        except Exception as exc:
            error_payload = {"type": "error", "detail": str(exc)}
            yield f"data: {json.dumps(error_payload, default=str)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
