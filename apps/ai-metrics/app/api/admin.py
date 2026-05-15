"""Admin 端点（generation service 暴露；query service 不挂）。

资源：
- /admin/projects            CRUD dim_project
- /admin/domains             CRUD dim_domain
- /admin/project_domains     CRUD 项目-领域映射（多对多）
- /admin/metrics             CRUD metric_def
- /admin/preagg/refresh      触发预聚合
- /admin/generate/simulate   触发数据仿真
"""
from __future__ import annotations
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, delete

from ..db import get_db
from ..schemas import Envelope
from ..models import (
    DimProject, DimDomain, DimProjectDomain, MetricDef,
)
from ..services.preagg import refresh as preagg_refresh


router = APIRouter(prefix="/admin")


# ════════ 预聚合 ════════════════════════════════════════════════
class PreaggRefreshReq(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    days_back: int | None = None


@router.post("/preagg/refresh", response_model=Envelope)
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


@router.post("/generate/simulate", response_model=Envelope)
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

@router.get("/projects", response_model=Envelope)
def list_projects(db: Session = Depends(get_db)):
    rows = db.execute(select(DimProject).order_by(DimProject.code)).scalars().all()
    return Envelope(data={"items": [
        {"id": r.id, "code": r.code, "name": r.name, "is_active": r.is_active}
        for r in rows
    ]})

@router.post("/projects", response_model=Envelope)
def upsert_project(req: ProjectIn, db: Session = Depends(get_db)):
    exist = db.execute(select(DimProject).where(DimProject.code == req.code)).scalar_one_or_none()
    if exist:
        exist.name = req.name; exist.is_active = req.is_active
    else:
        db.add(DimProject(**req.model_dump()))
    db.commit()
    return Envelope(data={"ok": True})

@router.delete("/projects/{code}", response_model=Envelope)
def delete_project(code: str, db: Session = Depends(get_db)):
    db.execute(delete(DimProject).where(DimProject.code == code))
    db.execute(delete(DimProjectDomain).where(DimProjectDomain.project_code == code))
    db.commit()
    return Envelope(data={"ok": True})


# ════════ 领域 CRUD ════════════════════════════════════════════════
class DomainIn(BaseModel):
    code: str
    name: str
    sort_order: int = 0

@router.get("/domains", response_model=Envelope)
def list_domains(db: Session = Depends(get_db)):
    rows = db.execute(select(DimDomain).order_by(DimDomain.sort_order, DimDomain.code)).scalars().all()
    return Envelope(data={"items": [
        {"id": r.id, "code": r.code, "name": r.name, "sort_order": r.sort_order}
        for r in rows
    ]})

@router.post("/domains", response_model=Envelope)
def upsert_domain(req: DomainIn, db: Session = Depends(get_db)):
    exist = db.execute(select(DimDomain).where(DimDomain.code == req.code)).scalar_one_or_none()
    if exist:
        exist.name = req.name; exist.sort_order = req.sort_order
    else:
        db.add(DimDomain(**req.model_dump()))
    db.commit()
    return Envelope(data={"ok": True})

@router.delete("/domains/{code}", response_model=Envelope)
def delete_domain(code: str, db: Session = Depends(get_db)):
    db.execute(delete(DimDomain).where(DimDomain.code == code))
    db.execute(delete(DimProjectDomain).where(DimProjectDomain.domain_code == code))
    db.commit()
    return Envelope(data={"ok": True})


# ════════ 项目-领域映射 ════════════════════════════════════════════════
class ProjectDomainSetReq(BaseModel):
    project_code: str
    domain_codes: list[str]       # 该项目映射到的领域全集（增量替换语义）

@router.get("/project_domains", response_model=Envelope)
def list_project_domains(db: Session = Depends(get_db)):
    rows = db.execute(select(DimProjectDomain).where(DimProjectDomain.is_active.is_(True))
                      .order_by(DimProjectDomain.project_code, DimProjectDomain.sort_order)).scalars().all()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r.project_code, []).append(r.domain_code)
    return Envelope(data={"items": [
        {"project_code": p, "domain_codes": ds} for p, ds in out.items()
    ]})

@router.post("/project_domains", response_model=Envelope)
def set_project_domains(req: ProjectDomainSetReq, db: Session = Depends(get_db)):
    db.execute(delete(DimProjectDomain).where(DimProjectDomain.project_code == req.project_code))
    for idx, dc in enumerate(req.domain_codes):
        db.add(DimProjectDomain(project_code=req.project_code, domain_code=dc,
                                is_active=True, sort_order=idx))
    db.commit()
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

@router.get("/metrics", response_model=Envelope)
def list_metrics(db: Session = Depends(get_db)):
    rows = db.execute(select(MetricDef).order_by(MetricDef.sort_order, MetricDef.code)).scalars().all()
    return Envelope(data={"items": [
        {"code": r.code, "label": r.label, "category": r.category, "unit": r.unit,
         "data_type": r.data_type, "agg_method": r.agg_method,
         "computed_formula": r.computed_formula, "weight_metric": r.weight_metric,
         "drilldown_enabled": r.drilldown_enabled, "is_default_visible": r.is_default_visible,
         "sort_order": r.sort_order}
        for r in rows
    ]})

@router.post("/metrics", response_model=Envelope)
def upsert_metric(req: MetricIn, db: Session = Depends(get_db)):
    exist = db.execute(select(MetricDef).where(MetricDef.code == req.code)).scalar_one_or_none()
    if exist:
        for k, v in req.model_dump().items():
            if k != "code": setattr(exist, k, v)
    else:
        db.add(MetricDef(**req.model_dump()))
    db.commit()
    return Envelope(data={"ok": True})

@router.delete("/metrics/{code}", response_model=Envelope)
def delete_metric(code: str, db: Session = Depends(get_db)):
    db.execute(delete(MetricDef).where(MetricDef.code == code))
    db.commit()
    return Envelope(data={"ok": True})
