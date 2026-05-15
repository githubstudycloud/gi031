from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import Envelope, SummaryRequest, DistinctRequest, DrilldownRequest
from ..services.metric_query import fetch_summary, fetch_distinct, fetch_drilldown


router = APIRouter()


@router.post("/reports/{report_type}/summary", response_model=Envelope)
def summary(report_type: str, req: SummaryRequest, db: Session = Depends(get_db)):
    result = fetch_summary(
        db,
        date_from=req.date_from,
        date_to=req.date_to,
        project_codes=req.project_codes,
        row_dim=req.row_dim,
    )
    # 分页：本接口默认全量（不分页），data 量受领域数控制；前端按需切
    rows = result["rows"]
    total = len(rows)
    if req.paging == "server":
        start = (req.page - 1) * req.page_size
        rows = rows[start: start + req.page_size]
    return Envelope(data={
        "items": rows,
        "page": req.page,
        "page_size": req.page_size if req.paging == "server" else total,
        "total": total,
        "has_more": False,
        "extras": {"totals_row": result["totals"]}
    })


@router.post("/reports/{report_type}/distinct", response_model=Envelope)
def distinct(report_type: str, req: DistinctRequest, db: Session = Depends(get_db)):
    items = fetch_distinct(
        db, column=req.column,
        date_from=req.date_from, date_to=req.date_to,
        project_codes=req.project_codes,
    )
    return Envelope(data={"column": req.column, "items": items, "truncated": False})


@router.post("/reports/{report_type}/drilldown", response_model=Envelope)
def drilldown(report_type: str, req: DrilldownRequest, db: Session = Depends(get_db)):
    domain_code = req.row.get("domain_code") or req.row.get("_domain_code_raw")
    metric_code = req.cell.get("column")
    bd = req.filter.get("business_date") or {}
    df = bd.get("from"); dt = bd.get("to")
    projects = req.filter.get("projects") or []
    if isinstance(projects, list) and projects and isinstance(projects[0], dict):
        projects = [p.get("value") for p in projects]
    paging = req.paging or {}
    page = int(paging.get("page", 1)); ps = int(paging.get("page_size", 50))
    data = fetch_drilldown(
        db,
        domain_code=domain_code,
        metric_code=metric_code,
        date_from=df, date_to=dt,
        project_codes=projects or None,
        page=page, page_size=ps,
    )
    return Envelope(data=data)
