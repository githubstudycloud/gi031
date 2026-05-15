from datetime import date
from typing import Any, Literal
from pydantic import BaseModel, Field


class Envelope(BaseModel):
    code: int = 0
    message: str = "ok"
    trace_id: str | None = None
    data: Any = None


class DateRange(BaseModel):
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    model_config = {"populate_by_name": True}


class SummaryRequest(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    project_codes: list[str] | None = None
    row_dim: str = "domain"                # V1.1 可改成 "project>domain" 等
    paging: Literal["server", "none"] = "server"
    page: int = 1
    page_size: int = 200
    sort: str | None = None                # "ai_code_lines:desc"
    row_filter: list[dict] | None = None


class DistinctRequest(BaseModel):
    column: str
    date_from: date | None = None
    date_to: date | None = None
    project_codes: list[str] | None = None


class DrilldownRequest(BaseModel):
    ref: str = "metric_drill"
    row: dict
    filter: dict
    cell: dict
    paging: dict | None = None
    sort: str | None = None


class IngestItem(BaseModel):
    period_date: date
    project_code: str
    domain_code: str
    iteration_code: str | None = None
    org_path: str | None = None
    metric_code: str
    metric_value: float
    source: str = "manual"


class IngestRequest(BaseModel):
    items: list[IngestItem]


class IngestDetail(BaseModel):
    period_date: date
    project_code: str
    domain_code: str
    iteration_code: str | None = None
    metric_code: str
    detail_type: str
    ref_id: str
    ref_label: str | None = None
    ref_url: str | None = None
    is_ai_generated: bool = False
    is_adopted: bool | None = None
    payload: dict | None = None


class IngestDetailRequest(BaseModel):
    items: list[IngestDetail]
