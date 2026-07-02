from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SupplierRead(ORMBase):
    id: int
    name: str | None = None
    code: str | None = None
    contact_email: str | None = None
    status: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProductRead(ORMBase):
    id: int
    supplier_id: int | None = None
    name: str | None = None
    sku: str | None = None
    part_number: str | None = None
    description: str | None = None
    category: str | None = None
    status: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LookupResult(BaseModel):
    entity_type: str
    count: int
    results: list[ProductRead | SupplierRead]


class RetrievalRequest(BaseModel):
    query: str


class IdentifierMatch(BaseModel):
    entity_type: str
    identifier_type: str
    value: str


class RetrievalResponse(BaseModel):
    query: str
    entity_type: str | None = None
    identifier_type: str | None = None
    matched_identifier: str | None = None
    primary: object | None = None
    product: ProductRead | None = None
    supplier: SupplierRead | None = None
    products: list[ProductRead] = Field(default_factory=list)
    suppliers: list[SupplierRead] = Field(default_factory=list)
    raw_records: dict[str, object] = Field(default_factory=dict)


class AgentQueryRequest(BaseModel):
    query: str


class AgentQueryResponse(BaseModel):
    query: str
    status: str = "unknown"
    answer: str | None = None
    clarification: str | None = None
    assessment: str | None = None
    confidence_score: float = 0.0
    consolidated_data: dict[str, object] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    reasoning_trace: list[dict[str, object]] = Field(default_factory=list)
    tool_calls: list[dict[str, object]] = Field(default_factory=list)
    executed_tools: list[str] = Field(default_factory=list)
    execution_plan: list[str] = Field(default_factory=list)
    execution_timeline: list[dict[str, object]] = Field(default_factory=list)
    execution_events: list[dict[str, object]] = Field(default_factory=list)
    error: str | None = None


class ToolInfo(BaseModel):
    name: str
    description: str
    parameters: dict[str, object] = Field(default_factory=dict)


class ToolExecuteRequest(BaseModel):
    tool: str
    tool_input: dict[str, object] = Field(default_factory=dict)


class ToolExecuteResponse(BaseModel):
    tool: str
    params: dict[str, object] = Field(default_factory=dict)
    success: bool
    data: object | None = None
    error: str | None = None
    execution_time_ms: float = 0.0
    total_time_ms: float = 0.0
