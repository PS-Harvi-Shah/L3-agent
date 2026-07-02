import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.observability.audit import AuditStore
from app.observability.evaluation import EvaluationEngine
from app.observability.metrics import MetricsCollector
from app.observability.trace import TraceManager


router = APIRouter(prefix="/agent", tags=["observability"])
logger = logging.getLogger("observability.router")


class ExecutionSummary(BaseModel):
    execution_id: str
    user_query: str
    timestamp: str
    execution_duration_ms: float = 0.0
    confidence_score: float = 0.0
    success: bool = True
    total_tools_executed: int = 0
    error: str | None = None


class ExecutionTraceResponse(BaseModel):
    execution_id: str
    user_query: str
    timestamp: str
    entity_type: str | None = None
    identifier_type: str | None = None
    identifier_value: str | None = None
    execution_plan: list[str] = Field(default_factory=list)
    executed_tools: list[str] = Field(default_factory=list)
    tool_execution_order: list[str] = Field(default_factory=list)
    execution_duration_ms: float = 0.0
    retrieved_entities: dict[str, Any] = Field(default_factory=dict)
    missing_entities: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    confidence_score: float = 0.0
    final_response: dict[str, Any] | None = None
    error: str | None = None
    workflow_steps: list[dict[str, Any]] = Field(default_factory=list)
    tool_latencies: dict[str, float] = Field(default_factory=dict)
    total_tools_executed: int = 0
    success: bool = True


class AggregatedMetrics(BaseModel):
    total_executions: int = 0
    success_count: int = 0
    failure_count: int = 0
    success_rate: float = 0.0
    failure_rate: float = 0.0
    avg_execution_time_ms: float = 0.0
    min_execution_time_ms: float = 0.0
    max_execution_time_ms: float = 0.0
    avg_tools_executed: float = 0.0
    min_tools_executed: int = 0
    max_tools_executed: int = 0
    avg_confidence: float = 0.0
    avg_tool_latencies_ms: dict[str, float] = Field(default_factory=dict)
    total_tool_types: int = 0
    avg_db_queries_per_execution: float = 0.0


class EvaluationResponse(BaseModel):
    execution_id: str
    timestamp: str
    overall_score: float = 0.0
    completeness: float = 0.0
    correctness: float = 0.0
    tool_selection_quality: float = 0.0
    overall_confidence: float = 0.0
    missing_count: int = 0
    total_expected: int = 0
    details: dict[str, Any] = Field(default_factory=dict)


def setup_observability() -> tuple[AuditStore, TraceManager, MetricsCollector, EvaluationEngine]:
    audit = AuditStore()
    tracer = TraceManager()
    metrics = MetricsCollector(audit)
    evaluator = EvaluationEngine(audit)
    return audit, tracer, metrics, evaluator


def trace_agent_execution(
    query: str,
    agent_result: dict[str, Any],
    start_time: float,
) -> str:
    audit, tracer, _metrics, _evaluator = setup_observability()
    trace = tracer.create_trace(query, start_time)
    end_time = time.perf_counter()
    trace = tracer.finalize_trace(trace, agent_result, start_time, end_time)
    tracer.compute_db_query_count(trace)
    audit.persist(trace)
    logger.info(
        "Execution traced",
        extra={
            "execution_id": trace.execution_id,
            "duration_ms": trace.execution_duration_ms,
            "tools": trace.total_tools_executed,
            "success": trace.success,
        },
    )
    return trace.execution_id


@router.get("/history", response_model=list[ExecutionSummary])
def list_executions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ExecutionSummary]:
    audit, _tracer, _metrics, _evaluator = setup_observability()
    executions = audit.list_executions(limit=limit, offset=offset)
    return [ExecutionSummary(**e) for e in executions]


@router.get("/execution/{execution_id}", response_model=ExecutionTraceResponse)
def get_execution(execution_id: str) -> ExecutionTraceResponse:
    audit, _tracer, _metrics, _evaluator = setup_observability()
    trace = audit.load(execution_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Execution {execution_id} not found")
    return ExecutionTraceResponse(**trace)


@router.get("/metrics", response_model=AggregatedMetrics)
def get_metrics() -> AggregatedMetrics:
    _audit, _tracer, metrics, _evaluator = setup_observability()
    result = metrics.get_aggregated_metrics()
    return AggregatedMetrics(**result)


@router.get("/evaluation/{execution_id}", response_model=EvaluationResponse)
def get_evaluation(execution_id: str) -> EvaluationResponse:
    _audit, _tracer, _metrics, evaluator = setup_observability()
    report = evaluator.evaluate(execution_id)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail=f"Execution {execution_id} not found or could not be evaluated",
        )
    return EvaluationResponse(**report.to_dict())
