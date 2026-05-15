"""管理端点（V1 占位，无鉴权）。"""
from __future__ import annotations
from datetime import date, timedelta
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import Envelope
from ..services.preagg import refresh as preagg_refresh


router = APIRouter(prefix="/admin")


class PreaggRefreshReq(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    days_back: int | None = None    # 二选一：date_from/to 或 days_back


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
    return Envelope(data={
        "deleted": deleted, "inserted": inserted,
        "date_from": f.isoformat(), "date_to": t.isoformat(),
    })
