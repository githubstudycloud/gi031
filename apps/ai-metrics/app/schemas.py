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


class VersionPin(BaseModel):
    period_date: date
    source: str
    version_no: int


class SummaryRequest(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    project_codes: list[str] | None = None
    row_dim: Literal["domain", "project>domain"] = "domain"
    paging: Literal["server", "none"] = "server"
    page: int = 1
    page_size: int = 200
    sort: str | None = None
    row_filter: list[dict] | None = None
    version_pin: list[VersionPin] | None = None
    # V3：同比 / 环比 / 自定义对比期
    # prev_period = 同长度前一段；prev_year = 去年同期；prev_month = 一个月前同长度
    compare_with: Literal["prev_period", "prev_year", "prev_month", "none"] | None = None


class DistinctRequest(BaseModel):
    column: str
    date_from: date | None = None
    date_to: date | None = None
    project_codes: list[str] | None = None


class DrilldownRequest(BaseModel):
    ref: str = "metric_drill"
    row: dict                              # {domain_code, project_code?}
    filter: dict                           # {business_date: {from,to}, projects: [...]}
    cell: dict                             # {column: <metric_code>}
    paging: dict | None = None             # {page, page_size}
    sort: str | None = None
    row_filter: list[dict] | None = None


class VersionMarkRequest(BaseModel):
    period_date: date
    source: str
    version_no: int
    reason: str | None = None
    valid: bool = False                    # True = unmark；False = mark invalid


class RowFavoriteRequest(BaseModel):
    row_key: dict                          # {domain_code: "..."} 或 {project_code, domain_code}
    favorited: bool


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
