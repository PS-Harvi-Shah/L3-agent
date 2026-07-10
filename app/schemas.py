from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SupplierRead(ORMBase):
    supplier_id: int
    supplier_name: str


class ProductRead(ORMBase):
    product_id: int
    product_name: str
    supplier_id: int
    part_number: str | None = None
    language: str | None = None
    country: str | None = None


class ToolInfo(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class AgentQueryRequest(BaseModel):
    query: str = Field(min_length=1, description="Any identifier: product id, part number, product name, supplier id, or supplier name")


class ToolCallRecord(BaseModel):
    step: int
    tool: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    success: bool
    record_count: int = 0
    error: str | None = None
    execution_time_ms: float = 0.0


class ReasoningStep(BaseModel):
    step: int
    thought: str | None = None
    action: str
    tool: str | None = None
    tool_input: dict[str, Any] | None = None
    llm_time_ms: float = 0.0


class AgentQueryResponse(BaseModel):
    execution_id: str
    query: str
    status: str
    answer: str | None = None
    consolidated_data: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    reasoning_trace: list[ReasoningStep] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    execution_events: list[dict[str, Any]] = Field(default_factory=list)
    duration_ms: float = 0.0
    error: str | None = None
