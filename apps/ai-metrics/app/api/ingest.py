from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy import insert
from io import StringIO
import csv

from ..db import get_db
from ..models import AiMetric, AiMetricDetail
from ..schemas import Envelope, IngestRequest, IngestDetailRequest
from .admin import require_admin_token


router = APIRouter(dependencies=[Depends(require_admin_token)])


def _upsert_metrics(db: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    # 简单 upsert：先按唯一键 delete-and-insert（跨 DB 兼容；并发场景需事务/行锁）
    inserted = 0
    for r in rows:
        existing = db.execute(
            AiMetric.__table__.select().where(
                AiMetric.period_date == r["period_date"],
                AiMetric.project_code == r["project_code"],
                AiMetric.domain_code == r["domain_code"],
                (AiMetric.iteration_code.is_(None) if r.get("iteration_code") is None
                 else AiMetric.iteration_code == r["iteration_code"]),
                AiMetric.metric_code == r["metric_code"],
                AiMetric.source == r.get("source", "manual"),
            )
        ).first()
        if existing:
            db.execute(
                AiMetric.__table__.update()
                .where(AiMetric.id == existing.id)
                .values(metric_value=r["metric_value"])
            )
        else:
            db.execute(insert(AiMetric).values(**r))
        inserted += 1
    db.commit()
    return inserted


@router.post("/metrics/ingest", response_model=Envelope)
def ingest_metrics(req: IngestRequest, db: Session = Depends(get_db)):
    rows = [item.model_dump() for item in req.items]
    n = _upsert_metrics(db, rows)
    return Envelope(data={"ingested": n})


@router.post("/metrics/ingest_csv", response_model=Envelope)
async def ingest_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """CSV 列顺序约定：
    period_date,project_code,domain_code,iteration_code,metric_code,metric_value,source
    """
    text = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(StringIO(text))
    rows = []
    for r in reader:
        rows.append({
            "period_date": r["period_date"],
            "project_code": r["project_code"],
            "domain_code": r["domain_code"],
            "iteration_code": r.get("iteration_code") or None,
            "metric_code": r["metric_code"],
            "metric_value": float(r["metric_value"]),
            "source": r.get("source") or "manual",
        })
    n = _upsert_metrics(db, rows)
    return Envelope(data={"ingested": n})


@router.post("/metrics/ingest_details", response_model=Envelope)
def ingest_details(req: IngestDetailRequest, db: Session = Depends(get_db)):
    cnt = 0
    for it in req.items:
        db.execute(insert(AiMetricDetail).values(**it.model_dump()))
        cnt += 1
    db.commit()
    return Envelope(data={"ingested": cnt})
