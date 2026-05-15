from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import Envelope
from ..services.config_assembler import assemble_config


router = APIRouter()


@router.get("/reports/{report_type}/config", response_model=Envelope)
def get_config(report_type: str, db: Session = Depends(get_db)):
    return Envelope(data=assemble_config(db, report_type))
