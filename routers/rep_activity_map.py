import logging
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.auth import UserResponse
from models.activity_logs import Activity_logs
from models.agent_logs import Agent_logs
from models.representatives import Representatives
from services.access import require_view_any, visible_user_ids

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
    await require_view_any(db, current_user, ("rep_activity_map", "map"))
    query = select(Activity_logs).where(
        Activity_logs.latitude.isnot(None),
        Activity_logs.longitude.isnot(None),
    )
    user_ids = await visible_user_ids(db, current_user)
    if user_ids is not None:
        query = query.where(Activity_logs.user_id.in_(user_ids))

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


class LivePosition(BaseModel):
    user_id: str
    rep_id: Optional[int] = None
    rep_name: Optional[str] = None
    lat: float
    lng: float
    timestamp: Optional[datetime] = None


class LivePositionsResponse(BaseModel):
    points: List[LivePosition]
    total: int


@router.get("/live", response_model=LivePositionsResponse)
async def get_live_rep_positions(
    minutes: int = Query(20, ge=1, le=180, description="How recent a ping must be to count as 'currently here'"),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Most recent GPS ping per rep within the last `minutes`, for a live
    "who's currently where" blip layer — separate from /map's historical
    visit points, which come from activity_logs rather than the
    continuous agent_logs location pings."""
    await require_view_any(db, current_user, ("rep_activity_map", "map"))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    query = (
        select(Agent_logs)
        .where(
            Agent_logs.timestamp >= cutoff,
            Agent_logs.latitude.isnot(None),
            Agent_logs.longitude.isnot(None),
        )
        .order_by(Agent_logs.timestamp.desc())
    )
    user_ids = await visible_user_ids(db, current_user)
    if user_ids is not None:
        query = query.where(Agent_logs.user_id.in_(user_ids))
    result = await db.execute(query)
    logs = result.scalars().all()

    # Keep only the newest ping per user (logs are already newest-first).
    latest_by_user: dict[str, Agent_logs] = {}
    for log in logs:
        if log.user_id not in latest_by_user:
            latest_by_user[log.user_id] = log

    rep_result = await db.execute(select(Representatives))
    rep_by_user_id = {r.user_id: r for r in rep_result.scalars().all()}

    points = [
        LivePosition(
            user_id=user_id,
            rep_id=rep_by_user_id[user_id].id if user_id in rep_by_user_id else None,
            rep_name=rep_by_user_id[user_id].name if user_id in rep_by_user_id else None,
            lat=log.latitude,
            lng=log.longitude,
            timestamp=log.timestamp,
        )
        for user_id, log in latest_by_user.items()
    ]

    return LivePositionsResponse(points=points, total=len(points))


class VisitCountBucket(BaseModel):
    pharmacy: int = 0
    doctor: int = 0
    total: int = 0


class VisitCountsResponse(BaseModel):
    today: VisitCountBucket
    yesterday: VisitCountBucket
    month: VisitCountBucket


_IRAQ_TZ = timezone(timedelta(hours=3))  # Baghdad, no DST


@router.get("/visit-counts", response_model=VisitCountsResponse)
async def get_visit_counts(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Pharmacy + doctor visit counts for today / yesterday / this month
    (Baghdad calendar days). A rep sees their own visits, a manager their
    assigned reps' (plus their own), everyone else sees all — the same
    rep/manager scoping used for customers and debts."""
    from services.area_scope import get_customer_scope

    now = datetime.now(_IRAQ_TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    month_start = today_start.replace(day=1)
    earliest = min(month_start, yesterday_start)

    query = select(Activity_logs.type, Activity_logs.timestamp).where(
        Activity_logs.type.in_(["pharmacy_visit", "doctor_visit"]),
        Activity_logs.timestamp >= earliest,
    )

    scope = await get_customer_scope(db, current_user)
    if not scope.unrestricted:
        reps_result = await db.execute(
            select(Representatives.user_id).where(Representatives.id.in_(scope.rep_ids))
        )
        user_ids = {row[0] for row in reps_result.all()} | {str(current_user.id)}
        query = query.where(Activity_logs.user_id.in_(user_ids))

    result = await db.execute(query)

    counts = {k: VisitCountBucket() for k in ("today", "yesterday", "month")}

    def add(bucket: VisitCountBucket, visit_type: str):
        if visit_type == "pharmacy_visit":
            bucket.pharmacy += 1
        else:
            bucket.doctor += 1
        bucket.total += 1

    for visit_type, ts in result.all():
        if ts is None:
            continue
        local = ts.astimezone(_IRAQ_TZ)
        if local >= today_start:
            add(counts["today"], visit_type)
        elif local >= yesterday_start:
            add(counts["yesterday"], visit_type)
        if local >= month_start:
            add(counts["month"], visit_type)

    return VisitCountsResponse(**counts)
