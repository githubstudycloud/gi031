from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..db import get_db
from ..models import DimProject
from ..schemas import Envelope


router = APIRouter()


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/dropdowns/projects", response_model=Envelope)
def dropdown_projects(
    q: str | None = Query(None, max_length=64),
    page: int = 1, page_size: int = 50,
    sort: str | None = None,
    only_favorites: int = 0,
    db: Session = Depends(get_db),
):
    """报表平台协议的 dropdown 端点。Project 当作 flat dropdown。"""
    stmt = select(DimProject).where(DimProject.is_active.is_(True))
    if q:
        like = f"%{q}%"
        stmt = stmt.where((DimProject.name.like(like)) | (DimProject.code.like(like)))

    # 简单排序：默认按 sort_order 不存在，先按 name；TODO: user favorite
    stmt = stmt.order_by(DimProject.code.asc())
    all_rows = db.execute(stmt).scalars().all()
    total = len(all_rows)
    paged = all_rows[(page - 1) * page_size: page * page_size]
    items = [{"value": p.code, "label": p.name, "is_favorite": False} for p in paged]
    return Envelope(data={
        "items": items, "page": page, "page_size": page_size,
        "total": total, "has_more": page * page_size < total
    })
