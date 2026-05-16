"""Admin 端点（**generation service 暴露；query service 不挂**）。

资源：
- /admin/_meta               服务自描述（admin UI 用来发现 query/generation URL；不鉴权）
- /admin/projects            CRUD dim_project
- /admin/domains             CRUD dim_domain
- /admin/project_domains     CRUD 项目-领域映射（多对多）
- /admin/metrics             CRUD metric_def
- /admin/preagg/refresh      触发预聚合
- /admin/generate/simulate   触发数据仿真

鉴权（V4）：
- `settings.admin_token` 非空时，所有 POST/DELETE 必须带 `X-Admin-Token: <token>`
- 空字符串 → 视为 dev 模式，启动会 warn 但不拦截

软删（V4）：
- `DELETE /admin/projects/{code}` 默认软删 (is_active=False)；带 `?force=true` 才硬删（并清相关映射）
- 同样适用于 domains / metrics
- 硬删不会自动清理 ai_metric / ai_metric_detail / report_fact_ai_metrics_daily 中的旧数据；
  软删足以让 UI 与查询过滤掉，老数据保留以便审计
"""
from __future__ import annotations
import logging
from datetime import date, timedelta
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select, delete

from ..db import get_db
from ..schemas import Envelope
from ..settings import settings
from ..models import (
    DimProject, DimDomain, DimProjectDomain, MetricDef,
)
from ..services.preagg import refresh as preagg_refresh


logger = logging.getLogger("ai-metrics.admin")
router = APIRouter(prefix="/admin")


# ════════ 鉴权 ════════════════════════════════════════════════
def require_admin_token(x_admin_token: str | None = Header(default=None)) -> None:
    """admin_token 非空时强制校验 header；空则允许（dev 模式）。"""
    if not settings.admin_token:
        return  # dev 模式：放行
    if not x_admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(401, "missing or invalid X-Admin-Token")


# ════════ Meta（用于 admin UI 发现服务拓扑）════════════════════════════
@router.get("/_meta", response_model=Envelope)
def admin_meta():
    """admin UI 启动时读这个；不鉴权（页面要先能拿到 has_auth 字段才知道要不要 prompt token）。"""
    return Envelope(data={
        "service": "generation",
        "has_auth": bool(settings.admin_token),
        "query_url": settings.query_url or "",
        "generation_url": settings.generation_url or "",
        "api_prefix": settings.api_prefix,
    })


# ════════ 预聚合 ════════════════════════════════════════════════
class PreaggRefreshReq(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    days_back: int | None = None


@router.post("/preagg/refresh", response_model=Envelope, dependencies=[Depends(require_admin_token)])
def post_preagg_refresh(req: PreaggRefreshReq, db: Session = Depends(get_db)):
    today = date.today()
    if req.date_from and req.date_to:
        f, t = req.date_from, req.date_to
    elif req.days_back is not None:
        f, t = today - timedelta(days=req.days_back), today
    else:
        f, t = today - timedelta(days=60), today
    deleted, inserted = preagg_refresh(f, t, db)
    return Envelope(data={"deleted": deleted, "inserted": inserted,
                          "date_from": f.isoformat(), "date_to": t.isoformat()})


# ════════ 数据仿真 ════════════════════════════════════════════════
class SimulateReq(BaseModel):
    sources: list[str] | None = None
    days: int = 1
    runs_per_day: int = 1
    reset: bool = False


@router.post("/generate/simulate", response_model=Envelope, dependencies=[Depends(require_admin_token)])
def post_simulate(req: SimulateReq):
    from ..sources.simulate import run as sim_run, SOURCES
    keys = req.sources or list(SOURCES.keys())
    for k in keys:
        if k not in SOURCES:
            raise HTTPException(400, f"unknown source: {k}")
    sim_run(keys, req.days, req.reset, req.runs_per_day)
    return Envelope(data={"ok": True, "sources": keys, "days": req.days})


# ════════ 项目 CRUD ════════════════════════════════════════════════
class ProjectIn(BaseModel):
    code: str
    name: str
    is_active: bool = True


def _safe_upsert(db: Session, model, code: str, new_values: dict) -> None:
    """并发安全 upsert：先尝试 select+update；若 select=None 则 add，IntegrityError 时重试为 update。

    覆盖 race condition：两个并发 insert 同时拿到 None，靠 UNIQUE 兜底再走 update 分支。
    """
    exist = db.execute(select(model).where(model.code == code)).scalar_one_or_none()
    if exist is not None:
        for k, v in new_values.items():
            setattr(exist, k, v)
        db.commit()
        return
    try:
        db.add(model(code=code, **new_values))
        db.commit()
    except IntegrityError:
        db.rollback()
        exist = db.execute(select(model).where(model.code == code)).scalar_one_or_none()
        if exist is None:
            raise
        for k, v in new_values.items():
            setattr(exist, k, v)
        db.commit()


@router.get("/projects", response_model=Envelope, dependencies=[Depends(require_admin_token)])
def list_projects(include_inactive: int = 0, db: Session = Depends(get_db)):
    stmt = select(DimProject).order_by(DimProject.code)
    if not include_inactive:
        stmt = stmt.where(DimProject.is_active.is_(True))
    rows = db.execute(stmt).scalars().all()
    return Envelope(data={"items": [
        {"id": r.id, "code": r.code, "name": r.name, "is_active": r.is_active}
        for r in rows
    ]})


@router.post("/projects", response_model=Envelope, dependencies=[Depends(require_admin_token)])
def upsert_project(req: ProjectIn, db: Session = Depends(get_db)):
    _safe_upsert(db, DimProject, req.code, {"name": req.name, "is_active": req.is_active})
    return Envelope(data={"ok": True})


@router.delete("/projects/{code}", response_model=Envelope, dependencies=[Depends(require_admin_token)])
def delete_project(code: str, force: int = 0, db: Session = Depends(get_db)):
    """默认软删 (is_active=False)；?force=1 硬删并级联映射表。

    硬删不清理 ai_metric / 详情表，保留历史数据以便审计。
    """
    if force:
        db.execute(delete(DimProject).where(DimProject.code == code))
        db.execute(delete(DimProjectDomain).where(DimProjectDomain.project_code == code))
        db.commit()
        return Envelope(data={"ok": True, "mode": "hard"})
    proj = db.execute(select(DimProject).where(DimProject.code == code)).scalar_one_or_none()
    if proj is None:
        raise HTTPException(404, f"project not found: {code}")
    proj.is_active = False
    db.commit()
    return Envelope(data={"ok": True, "mode": "soft"})


# ════════ 领域 CRUD ════════════════════════════════════════════════
class DomainIn(BaseModel):
    code: str
    name: str
    sort_order: int = 0


@router.get("/domains", response_model=Envelope, dependencies=[Depends(require_admin_token)])
def list_domains(include_inactive: int = 0, db: Session = Depends(get_db)):
    stmt = select(DimDomain).order_by(DimDomain.sort_order, DimDomain.code)
    if not include_inactive:
        stmt = stmt.where(DimDomain.is_active.is_(True))
    rows = db.execute(stmt).scalars().all()
    return Envelope(data={"items": [
        {"id": r.id, "code": r.code, "name": r.name, "sort_order": r.sort_order,
         "is_active": r.is_active}
        for r in rows
    ]})


@router.post("/domains", response_model=Envelope, dependencies=[Depends(require_admin_token)])
def upsert_domain(req: DomainIn, db: Session = Depends(get_db)):
    _safe_upsert(db, DimDomain, req.code, {"name": req.name, "sort_order": req.sort_order})
    return Envelope(data={"ok": True})


@router.delete("/domains/{code}", response_model=Envelope, dependencies=[Depends(require_admin_token)])
def delete_domain(code: str, force: int = 0, db: Session = Depends(get_db)):
    if force:
        db.execute(delete(DimDomain).where(DimDomain.code == code))
        db.execute(delete(DimProjectDomain).where(DimProjectDomain.domain_code == code))
        db.commit()
        return Envelope(data={"ok": True, "mode": "hard"})
    dom = db.execute(select(DimDomain).where(DimDomain.code == code)).scalar_one_or_none()
    if dom is None:
        raise HTTPException(404, f"domain not found: {code}")
    dom.is_active = False
    db.commit()
    return Envelope(data={"ok": True, "mode": "soft"})


# ════════ 项目-领域映射 ════════════════════════════════════════════════
class ProjectDomainSetReq(BaseModel):
    project_code: str
    domain_codes: list[str]       # 该项目映射到的领域全集（增量替换语义）


@router.get("/project_domains", response_model=Envelope, dependencies=[Depends(require_admin_token)])
def list_project_domains(db: Session = Depends(get_db)):
    rows = db.execute(select(DimProjectDomain).where(DimProjectDomain.is_active.is_(True))
                      .order_by(DimProjectDomain.project_code, DimProjectDomain.sort_order)).scalars().all()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r.project_code, []).append(r.domain_code)
    return Envelope(data={"items": [
        {"project_code": p, "domain_codes": ds} for p, ds in out.items()
    ]})


@router.post("/project_domains", response_model=Envelope, dependencies=[Depends(require_admin_token)])
def set_project_domains(req: ProjectDomainSetReq, db: Session = Depends(get_db)):
    """整组替换语义；在单个事务里做 delete+insert，失败回滚保护原状态。"""
    try:
        db.execute(delete(DimProjectDomain).where(DimProjectDomain.project_code == req.project_code))
        for idx, dc in enumerate(req.domain_codes):
            db.add(DimProjectDomain(project_code=req.project_code, domain_code=dc,
                                    is_active=True, sort_order=idx))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return Envelope(data={"ok": True, "set_count": len(req.domain_codes)})


# ════════ metric_def CRUD ════════════════════════════════════════════════
class MetricIn(BaseModel):
    code: str
    label: str
    category: str
    unit: str | None = None
    data_type: str = "int"
    agg_method: str = "sum"
    computed_formula: str | None = None
    weight_metric: str | None = None
    drilldown_enabled: bool = False
    is_default_visible: bool = True
    sort_order: int = 0
    is_active: bool = True


@router.get("/metrics", response_model=Envelope, dependencies=[Depends(require_admin_token)])
def list_metrics(include_inactive: int = 0, db: Session = Depends(get_db)):
    stmt = select(MetricDef).order_by(MetricDef.sort_order, MetricDef.code)
    if not include_inactive:
        stmt = stmt.where(MetricDef.is_active.is_(True))
    rows = db.execute(stmt).scalars().all()
    return Envelope(data={"items": [
        {"code": r.code, "label": r.label, "category": r.category, "unit": r.unit,
         "data_type": r.data_type, "agg_method": r.agg_method,
         "computed_formula": r.computed_formula, "weight_metric": r.weight_metric,
         "drilldown_enabled": r.drilldown_enabled, "is_default_visible": r.is_default_visible,
         "sort_order": r.sort_order, "is_active": r.is_active}
        for r in rows
    ]})


@router.post("/metrics", response_model=Envelope, dependencies=[Depends(require_admin_token)])
def upsert_metric(req: MetricIn, db: Session = Depends(get_db)):
    payload = req.model_dump()
    code = payload.pop("code")
    _safe_upsert(db, MetricDef, code, payload)
    return Envelope(data={"ok": True})


@router.delete("/metrics/{code}", response_model=Envelope, dependencies=[Depends(require_admin_token)])
def delete_metric(code: str, force: int = 0, db: Session = Depends(get_db)):
    if force:
        db.execute(delete(MetricDef).where(MetricDef.code == code))
        db.commit()
        return Envelope(data={"ok": True, "mode": "hard"})
    m = db.execute(select(MetricDef).where(MetricDef.code == code)).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, f"metric not found: {code}")
    m.is_active = False
    db.commit()
    return Envelope(data={"ok": True, "mode": "soft"})
