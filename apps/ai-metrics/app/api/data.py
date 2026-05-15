from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session
from datetime import date

from ..db import get_db
from ..schemas import (
    Envelope, SummaryRequest, DistinctRequest, DrilldownRequest,
    VersionMarkRequest, RowFavoriteRequest,
)
from ..services.metric_query import (
    fetch_summary, fetch_distinct, fetch_drilldown,
    list_versions, mark_version_invalid, unmark_version,
    toggle_row_favorite,
)


router = APIRouter()


def _user_id(x_user_id: str | None = Header(default=None)) -> str:
    """没有真正认证；从 header 取，默认 'demo-user'。"""
    return x_user_id or "demo-user"


@router.post("/reports/{report_type}/summary", response_model=Envelope)
def summary(report_type: str, req: SummaryRequest,
            db: Session = Depends(get_db),
            user_id: str = Depends(_user_id)):
    pins = [v.model_dump() for v in req.version_pin] if req.version_pin else None
    result = fetch_summary(
        db,
        date_from=req.date_from,
        date_to=req.date_to,
        project_codes=req.project_codes,
        row_dim=req.row_dim,
        user_id=user_id,
        row_filter=req.row_filter,
        sort=req.sort,
        page=req.page,
        page_size=req.page_size,
        paging_mode=req.paging,
        version_pin=pins,
    )
    return Envelope(data={
        "items": result["rows"],
        "page": req.page,
        "page_size": req.page_size if req.paging == "server" else result["total"],
        "total": result["total"],
        "has_more": req.paging == "server" and req.page * req.page_size < result["total"],
        "extras": {
            "totals_row": result["totals"],
            "valid_versions": result["valid_versions"],
            "row_dim": req.row_dim,
        }
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
    metric_code = req.cell.get("column")
    domain_code = req.row.get("_domain_code_raw") or req.row.get("domain_code")
    project_code = req.row.get("_project_code_raw") or req.row.get("project_code")
    bd = req.filter.get("business_date") or {}
    df = bd.get("from"); dt = bd.get("to")
    if isinstance(df, str): df = date.fromisoformat(df)
    if isinstance(dt, str): dt = date.fromisoformat(dt)
    projects = req.filter.get("projects") or []
    if isinstance(projects, list) and projects and isinstance(projects[0], dict):
        projects = [p.get("value") for p in projects]
    paging = req.paging or {}
    page = int(paging.get("page", 1)); ps = int(paging.get("page_size", 50))

    data = fetch_drilldown(
        db,
        domain_code=domain_code,
        # 如果 row_dim=domain，project_code 是 "合计" 文字而非 raw；用 None
        project_code=(project_code if (project_code and project_code != "合计" and not domain_code or True) else None),
        metric_code=metric_code,
        date_from=df, date_to=dt,
        project_codes=projects or None,
        row_filter=req.row_filter,
        sort=req.sort,
        page=page, page_size=ps,
    )
    return Envelope(data=data)


# ──────── 版本管理 ────────

@router.get("/reports/{report_type}/versions", response_model=Envelope)
def get_versions(report_type: str,
                 date_from: date | None = None, date_to: date | None = None,
                 source: str | None = None,
                 db: Session = Depends(get_db)):
    sources = source.split(",") if source else None
    items = list_versions(db, date_from, date_to, sources)
    return Envelope(data={"items": items})


@router.post("/reports/{report_type}/versions/mark", response_model=Envelope)
def post_mark_version(report_type: str, req: VersionMarkRequest,
                      db: Session = Depends(get_db),
                      user_id: str = Depends(_user_id)):
    if req.valid:
        unmark_version(db, req.period_date, req.source, req.version_no)
    else:
        mark_version_invalid(db, req.period_date, req.source, req.version_no, user_id, req.reason)
    return Envelope(data={"ok": True})


# ──────── 行收藏 ────────

@router.post("/users/me/row_favorites/{report_type}", response_model=Envelope)
def post_row_favorite(report_type: str, req: RowFavoriteRequest,
                      db: Session = Depends(get_db),
                      user_id: str = Depends(_user_id)):
    toggle_row_favorite(db, user_id, report_type, req.row_key, req.favorited)
    return Envelope(data={"ok": True})
