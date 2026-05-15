from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..db import get_db
from ..models import ViewTemplate
from ..schemas import Envelope


router = APIRouter()


@router.get("/view_templates/{report_type}", response_model=Envelope)
def list_view_templates(report_type: str, db: Session = Depends(get_db)):
    """返回该 report_type 下后端注册的所有 view templates。
    前端有内置模板（默认/紧凑/看板）写死在 JS；后端这里返回**自定义**模板。"""
    rows = db.execute(
        select(ViewTemplate)
        .where(ViewTemplate.report_type == report_type, ViewTemplate.is_active.is_(True))
        .order_by(ViewTemplate.sort_order, ViewTemplate.code)
    ).scalars().all()
    items = [{
        "code": r.code, "name": r.name, "scope": r.scope,
        "source": "backend", "config": r.config
    } for r in rows]
    return Envelope(data={"items": items})
