import logging
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.auth import UserResponse
from models.activity_logs import Activity_logs
from models.representatives import Representatives

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rep-activity", tags=["rep-activity"])


class ActivityPoint(BaseModel):
    id: int
    lat: float
    lng: float
    visit_type: Optional[str] = None
    timestamp: Optional[datetime] = None
    pharmacy_name: Optional[str] = None
    doctor_name: Optional[str] = None
    rep_id: Optional[int] = None
    rep_name: Optional[str] = None


class RepActivityMapResponse(BaseModel):
    points: List[ActivityPoint]
    total: int
    rep_name: Optional[str] = None


@router.get("/map", response_model=RepActivityMapResponse)
async def get_rep_activity_map(
    rep_id: Optional[int] = Query(None),
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Geotagged field-visit points for one rep (or all reps) within a date range,
    for plotting movement/density on a map."""
    query = select(Activity_logs).where(
        Activity_logs.latitude.isnot(None),
        Activity_logs.longitude.isnot(None),
    )

    if rep_id is not None:
        query = query.where(Activity_logs.rep_id == rep_id)

    if start_date:
        try:
            start_d = date.fromisoformat(start_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="تنسيق تاريخ البداية غير صحيح")
        query = query.where(cast(Activity_logs.timestamp, Date) >= start_d)

    if end_date:
        try:
            end_d = date.fromisoformat(end_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="تنسيق تاريخ النهاية غير صحيح")
        query = query.where(cast(Activity_logs.timestamp, Date) <= end_d)

    query = query.order_by(Activity_logs.timestamp.desc()).limit(3000)
    result = await db.execute(query)
    logs = result.scalars().all()

    rep_name = None
    rep_name_map = {}
    rep_ids = {log.rep_id for log in logs if log.rep_id} | ({rep_id} if rep_id else set())
    if rep_ids:
        rep_result = await db.execute(select(Representatives).where(Representatives.id.in_(rep_ids)))
        rep_name_map = {r.id: r.name for r in rep_result.scalars().all()}
        if rep_id is not None:
            rep_name = rep_name_map.get(rep_id)

    points = [
        ActivityPoint(
            id=log.id,
            lat=log.latitude,
            lng=log.longitude,
            visit_type=log.visit_type,
            timestamp=log.timestamp,
            pharmacy_name=log.pharmacy_name,
            doctor_name=log.doctor_name,
            rep_id=log.rep_id,
            rep_name=rep_name_map.get(log.rep_id) if log.rep_id else None,
        )
        for log in logs
    ]

    return RepActivityMapResponse(points=points, total=len(points), rep_name=rep_name)
